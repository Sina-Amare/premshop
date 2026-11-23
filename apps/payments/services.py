"""Cart checkout; payment initiation and verification are separate operations."""

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables

from apps.accounts.models import User
from apps.accounts.otp import normalize_email
from apps.cart.models import Cart, CartItem
from apps.catalog.inputs import normalize_customer_input
from apps.orders import holiday
from apps.orders.models import Order, OrderItem
from apps.orders.selectors import current_unpaid_order
from apps.orders.services import (
    OrderLine,
    Purchase,
    UnpaidOrderExists,
    can_pay_order,
    expire_unpaid_orders,
    mark_item_refunded,
    mark_order_paid,
    place_order,
    quote_order,
    replay_checkout,
)
from apps.payments.models import Payment, Refund

QUOTE_SALT = "payments.quote"
QUOTE_AGE = 15 * 60


def _payment_operator(actor: User, permission: str = "payments.add_payment") -> User:
    account = User.objects.filter(pk=actor.pk, is_active=True, is_staff=True).first()
    if account is None or not account.has_perms(
        [permission, "orders.change_order", "orders.change_orderitem"]
    ):
        raise PermissionDenied
    return account


def _payment_order(order_id: int) -> Order:
    owner_id = Order.objects.values_list("user_id", flat=True).get(pk=order_id)
    User.objects.select_for_update(no_key=True).get(pk=owner_id)
    return Order.objects.select_for_update().get(pk=order_id)


def _received_amount(amount: Decimal, expected: Decimal) -> None:
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount != expected:
        raise ValidationError(
            _("پرداخت ثبت نشد، چون مبلغ دریافتی با مبلغ نهایی سفارش دقیقاً برابر نیست.")
        )


def _bank_reference(bank_ref: str) -> str:
    if not isinstance(bank_ref, str) or not bank_ref.strip() or len(bank_ref) > 64:
        raise ValidationError(
            _("شماره‌ی پیگیری بانک را از رسید واریز، با حداکثر ۶۴ کاراکتر، وارد کنید.")
        )
    return bank_ref.strip()


@sensitive_variables()
@transaction.atomic
def record_manual_payment(*, order_id: int, actor: User, amount: Decimal, bank_ref: str) -> Payment:
    account = _payment_operator(actor)
    order = _payment_order(order_id)
    _received_amount(amount, order.total_amount)
    bank_ref = _bank_reference(bank_ref)
    verified = order.payments.select_for_update().filter(status=Payment.Status.VERIFIED).first()
    if verified is not None:
        if verified.method != Payment.Method.MANUAL or verified.bank_ref != bank_ref:
            raise ValidationError(
                _(
                    "پرداخت این سفارش قبلاً با شماره‌ی پیگیری یا روش پرداخت دیگری ثبت و تأیید شده است و به همین دلیل پرداخت دوباره‌ای برای آن ثبت نشد."
                )
            )
        return confirm_payment(payment_id=verified.pk, actor=account)
    if not can_pay_order(order):
        raise ValidationError(
            _(
                "پرداخت این سفارش ثبت نشد، چون سفارش لغو شده، مهلت پرداخت آن تمام شده یا پرداخت دیگری برای آن در جریان است."
            )
        )
    payment = Payment.objects.create(
        order=order,
        method=Payment.Method.MANUAL,
        amount=order.total_amount,
        bank_ref=bank_ref,
        matched_by=account,
    )
    return confirm_payment(payment_id=payment.pk, actor=account)


@transaction.atomic
def confirm_payment(*, payment_id: int, actor: User) -> Payment:
    account = _payment_operator(actor)
    order = _payment_order(Payment.objects.values_list("order_id", flat=True).get(pk=payment_id))
    payment = Payment.objects.select_for_update().get(pk=payment_id, order=order)
    _received_amount(payment.amount, order.total_amount)
    if payment.status == Payment.Status.VERIFIED:
        return payment
    if payment.method != Payment.Method.MANUAL or payment.status != Payment.Status.CREATED:
        raise ValidationError(
            _(
                "این پرداخت از درگاه یا با وضعیتی دیگر ثبت شده است و امکان تأیید آن از بخش ثبت پرداخت خارج از درگاه وجود ندارد."
            )
        )
    _bank_reference(payment.bank_ref)
    if payment.matched_by_id != account.pk:
        raise PermissionDenied
    # Before the time is stamped, so a holiday cannot end between it and the clock's start.
    holiday.lock()
    payment.status, payment.verified_at = Payment.Status.VERIFIED, timezone.now()
    payment.save(update_fields=["status", "verified_at"])
    mark_order_paid(order=order, payment=payment, actor=account)
    return payment


@sensitive_variables()
@transaction.atomic
def execute_refunds(
    *,
    order_id: int,
    refund_ids: Sequence[int],
    actor: User,
    amount: Decimal,
    destination_kind: str,
    destination_last4: str,
    bank_ref: str,
    executed_at: datetime,
) -> list[Refund]:
    account = _payment_operator(actor, "payments.change_refund")
    if (
        not isinstance(refund_ids, (list, tuple))
        or not refund_ids
        or any(type(pk) is not int or pk <= 0 for pk in refund_ids)
        or len(set(refund_ids)) != len(refund_ids)
    ):
        raise ValidationError(_("بازپرداخت‌هایی را که این واریز پوشش می‌دهد از فهرست انتخاب کنید."))
    bank_ref = _bank_reference(bank_ref)
    if isinstance(destination_last4, str):
        destination_last4 = destination_last4.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        )
    if (
        destination_kind not in Refund.Destination.values
        or not isinstance(destination_last4, str)
        or not re.fullmatch(r"[0-9]{4}", destination_last4)
    ):
        raise ValidationError(
            _("نوع مقصد واریز را انتخاب و چهار رقم آخر کارت یا شبا را با عدد وارد کنید.")
        )
    if (
        not isinstance(executed_at, datetime)
        or timezone.is_naive(executed_at)
        or executed_at > timezone.now()
    ):
        raise ValidationError(
            _("زمان واریز را از رسید بانک وارد کنید و این زمان نمی‌تواند بعد از همین لحظه باشد.")
        )
    order = _payment_order(order_id)
    if not order.payments.select_for_update().filter(status=Payment.Status.VERIFIED).exists():
        raise ValidationError(
            _(
                "برای این سفارش هیچ پرداخت تأییدشده‌ای ثبت نشده است و به همین دلیل واریز بازپرداختی هم برای آن ثبت نمی‌شود."
            )
        )
    item_ids = Refund.objects.filter(pk__in=refund_ids, order_item__order=order).values(
        "order_item_id"
    )
    items = {
        item.pk: item
        for item in OrderItem.objects.select_for_update()
        .filter(order=order, pk__in=item_ids)
        .defer("customer_input")
        .order_by("pk")
    }
    refunds = list(
        Refund.objects.select_for_update()
        .filter(pk__in=refund_ids, order_item_id__in=items)
        .order_by("pk")
    )
    if len(refunds) != len(refund_ids):
        raise ValidationError(
            _(
                "یکی از بازپرداخت‌های انتخاب‌شده به این سفارش تعلق ندارد و برای انتخاب دوباره‌ی بازپرداخت‌ها، صفحه را دوباره باز کنید."
            )
        )
    if any(refund.status != Refund.Status.OWED for refund in refunds):
        raise ValidationError(
            _(
                "واریز یکی از بازپرداخت‌های انتخاب‌شده قبلاً ثبت شده است و به همین دلیل هیچ واریز جدیدی ثبت نشد."
            )
        )
    if any(items[refund.order_item_id].status != OrderItem.Status.CANCELLED for refund in refunds):
        raise ValidationError(
            _("واریز ثبت نشد، چون یکی از محصولات این بازپرداخت‌ها دیگر در وضعیت لغوشده نیست.")
        )
    if (
        not isinstance(amount, Decimal)
        or not amount.is_finite()
        or amount <= 0
        or amount != amount.to_integral_value()
        or amount != sum(refund.amount for refund in refunds)
    ):
        raise ValidationError(
            _("واریز ثبت نشد، چون مبلغ واریز با جمع بازپرداخت‌های انتخاب‌شده دقیقاً برابر نیست.")
        )
    # The bank-time field displays seconds, not the database's fractional seconds.
    if executed_at < max(refund.created_at for refund in refunds).replace(microsecond=0):
        raise ValidationError(
            _(
                "زمان واریز نمی‌تواند پیش از زمان ثبت این بازپرداخت باشد و لازم است زمان را از رسید بانک دوباره وارد کنید."
            )
        )
    for refund in refunds:
        refund.status = Refund.Status.EXECUTED
        refund.destination_kind, refund.destination_last4 = destination_kind, destination_last4
        refund.bank_ref, refund.executed_by, refund.executed_at = bank_ref, account, executed_at
        refund.save(
            update_fields=[
                "status",
                "destination_kind",
                "destination_last4",
                "bank_ref",
                "executed_by",
                "executed_at",
            ]
        )
        mark_item_refunded(item=items[refund.order_item_id], refund=refund, actor=account)
    return refunds


@dataclass(frozen=True)
class CheckoutQuote:
    purchase: Purchase
    token: str


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _cart_digest(cart: Cart, items: list[CartItem]) -> str:
    return hashlib.sha256(
        _json(
            [
                cart.pk,
                cart.updated_at.isoformat(),
                [[item.pk, item.plan_id, item.quantity] for item in items],
            ]
        ).encode()
    ).hexdigest()


def _changed() -> ValidationError:
    return ValidationError(
        "سبد خرید، قیمت یا شرایط خرید پس از باز شدن این صفحه تغییر کرده است و اگر با مبلغ و شرایط جدیدی که در همین صفحه آمده است موافقید، دوباره «ثبت سفارش» را بزنید.",
        code="quote_changed",
    )


def _account(user: User, *, lock: bool) -> User:
    query = User.objects.select_for_update(no_key=True) if lock else User.objects.all()
    account = query.filter(pk=user.pk, is_active=True).first() if user.pk else None
    if account is None:
        raise PermissionDenied
    return account


def quote_checkout(*, user: User, discount_code: str = "") -> CheckoutQuote:
    account = _account(user, lock=False)
    if existing := current_unpaid_order(account):
        raise UnpaidOrderExists(existing)
    cart = Cart.objects.filter(user=account).first()
    items = list(cart.items.select_related("plan").order_by("pk")) if cart else []
    if cart is None or not items:
        raise ValidationError(
            "سبد خرید شما خالی است و برای ثبت سفارش، اول محصولی به آن اضافه کنید."
        )
    purchase = quote_order(
        user=account,
        lines=[OrderLine(item.plan, item.quantity) for item in items],
        discount_code=discount_code,
    )
    payload = {
        "version": 2,
        "user": account.pk,
        "cart": cart.pk,
        "cart_digest": _cart_digest(cart, items),
        "terms": purchase.terms_digest(),
        "code": purchase.code.code if purchase.code else "",
        "inputs": [[line.plan.pk, line.plan.input_type] for line in purchase.lines],
    }
    return CheckoutQuote(purchase, signing.dumps(payload, salt=QUOTE_SALT, compress=True))


def prepare_checkout(*, user: User) -> None:
    """An explicit review POST can release this customer's overdue claims."""
    account = _account(user, lock=False)
    expire_unpaid_orders(user_id=account.pk)


def _payload(token: str, user: User) -> dict[str, Any]:
    try:
        if not isinstance(token, str) or len(token) > 20000:
            raise signing.BadSignature
        payload = signing.loads(token, salt=QUOTE_SALT)
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 2
            or payload.get("user") != user.pk
            or type(payload.get("cart")) is not int
            or not re.fullmatch(r"[0-9a-f]{64}", payload.get("cart_digest", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", payload.get("terms", ""))
            or not isinstance(payload.get("code"), str)
            or not isinstance(payload.get("inputs"), list)
            or not 1 <= len(payload["inputs"]) <= 100
            or any(
                not isinstance(line, list)
                or len(line) != 2
                or type(line[0]) is not int
                or line[1] not in ("none", "email", "username")
                for line in payload["inputs"]
            )
            or len({line[0] for line in payload["inputs"]}) != len(payload["inputs"])
        ):
            raise signing.BadSignature
        return payload
    except signing.BadSignature, ValueError, TypeError:
        raise ValidationError(
            "اطلاعات این صفحه دیگر معتبر نیست و برای دیدن مبلغ و شرایط به‌روز و ثبت سفارش، صفحه را دوباره باز کنید."
        ) from None


def _check_quote_age(token: str) -> None:
    try:
        signing.loads(token, salt=QUOTE_SALT, max_age=QUOTE_AGE)
    except signing.SignatureExpired:
        raise ValidationError(
            "از باز شدن این صفحه مدتی گذشته است و اگر با قیمت و موجودی به‌روزی که در همین صفحه آمده است موافقید، دوباره «ثبت سفارش» را بزنید.",
            code="quote_changed",
        ) from None


@transaction.atomic
def checkout(
    *,
    user: User,
    quote: str,
    checkout_id: uuid.UUID,
    customer_inputs: Mapping[str, str | None],
    email: str = "",
    discount_code: str | None = None,
) -> Order:
    account = _account(user, lock=True)
    payload = _payload(quote, account)
    if discount_code is not None and (
        not isinstance(discount_code, str)
        or len(discount_code) > 32
        or discount_code.strip().upper() != payload["code"]
    ):
        raise _changed()
    if not isinstance(checkout_id, uuid.UUID) or not isinstance(customer_inputs, Mapping):
        raise ValidationError(
            "اطلاعات این سفارش کامل یا معتبر نیست و برای ثبت سفارش، لازم است صفحه را دوباره باز کنید."
        )
    allowed = {str(line[0]) for line in payload["inputs"]}
    if set(customer_inputs) - allowed:
        raise ValidationError(
            "اطلاعات این سفارش کامل یا معتبر نیست و برای ثبت سفارش، لازم است صفحه را دوباره باز کنید."
        )
    inputs = {
        str(plan_id): normalize_customer_input(kind, customer_inputs.get(str(plan_id)))
        for plan_id, kind in payload["inputs"]
    }
    if not isinstance(email, str):
        raise ValidationError(
            "ایمیلی که وارد کرده‌اید درست نیست و لازم است نشانی کامل ایمیل خود را با دقت دوباره بنویسید."
        )
    email = normalize_email(email)
    submission = _json(
        {
            "quote": payload,
            "inputs": inputs,
            "email": email,
        }
    )
    digests = [
        salted_hmac("payments.checkout", submission, secret=secret, algorithm="sha256").hexdigest()
        for secret in [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
    ]
    stored = (
        Order.objects.filter(checkout_id=checkout_id, user=account)
        .values_list(
            "submission_digest",
            flat=True,
        )
        .first()
    )
    digest = next(
        (value for value in digests if stored and constant_time_compare(stored, value)), digests[0]
    )
    if replay := replay_checkout(user=account, checkout_id=checkout_id, submission_digest=digest):
        return replay
    _check_quote_age(quote)
    if existing := current_unpaid_order(account):
        raise UnpaidOrderExists(existing)
    cart = Cart.objects.select_for_update().filter(pk=payload["cart"], user=account).first()
    items = list(cart.items.select_related("plan").order_by("pk")) if cart else []
    if cart is None or not items or _cart_digest(cart, items) != payload["cart_digest"]:
        raise _changed()
    order = place_order(
        user=account,
        lines=[
            OrderLine(item.plan, item.quantity, inputs.get(str(item.plan_id))) for item in items
        ],
        email=email,
        discount_code=payload["code"],
        checkout_id=checkout_id,
        submission_digest=digest,
        expected_terms=payload["terms"],
    )
    _check_quote_age(quote)
    cart.items.all().delete()
    Cart.objects.filter(pk=cart.pk).update(updated_at=timezone.now())
    return order

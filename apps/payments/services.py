"""Cart checkout; payment initiation and verification are separate operations."""

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.accounts.models import User
from apps.accounts.validators import normalize_mobile_phone
from apps.cart.models import Cart, CartItem
from apps.catalog.inputs import normalize_customer_input
from apps.orders.models import Order, OrderItem
from apps.orders.services import (
    OrderLine,
    Purchase,
    UnpaidOrderExists,
    expire_unpaid_orders,
    place_order,
    quote_order,
    replay_checkout,
)

QUOTE_SALT = "payments.quote"
QUOTE_AGE = 15 * 60


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
        "سبد خرید، قیمت یا شرایط خرید تغییر کرده است. خلاصه‌ی جدید را بررسی و دوباره تأیید کنید.",
        code="quote_changed",
    )


def _account(user: User, *, lock: bool) -> User:
    query = User.objects.select_for_update(no_key=True) if lock else User.objects.all()
    account = query.filter(pk=user.pk, is_active=True).first() if user.pk else None
    if account is None:
        raise PermissionDenied
    return account


def current_unpaid_order(user: User) -> Order | None:
    pending = OrderItem.objects.filter(
        order_id=OuterRef("pk"),
        status=OrderItem.Status.PENDING_PAYMENT,
    )
    return (
        Order.objects.filter(
            user_id=user.pk,
            unpaid_expires_at__gt=timezone.now(),
        )
        .filter(Exists(pending))
        .order_by("pk")
        .first()
    )


def quote_checkout(*, user: User, discount_code: str = "") -> CheckoutQuote:
    account = _account(user, lock=False)
    if existing := current_unpaid_order(account):
        raise UnpaidOrderExists(existing)
    cart = Cart.objects.filter(user=account).first()
    items = list(cart.items.select_related("plan").order_by("pk")) if cart else []
    if cart is None or not items:
        raise ValidationError("سبد خرید شما خالی است.")
    purchase = quote_order(
        user=account,
        lines=[OrderLine(item.plan, item.quantity) for item in items],
        discount_code=discount_code,
    )
    payload = {
        "version": 1,
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
            or payload.get("version") != 1
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
        raise ValidationError("خلاصه‌ی خرید معتبر نیست. صفحه را دوباره باز کنید.") from None


def _check_quote_age(token: str) -> None:
    try:
        signing.loads(token, salt=QUOTE_SALT, max_age=QUOTE_AGE)
    except signing.SignatureExpired:
        raise ValidationError(
            "مهلت تأیید خلاصه‌ی خرید تمام شده است. خلاصه‌ی جدید را بررسی کنید.",
            code="quote_changed",
        ) from None


@transaction.atomic
def checkout(
    *,
    user: User,
    quote: str,
    checkout_id: uuid.UUID,
    customer_inputs: Mapping[str, str | None],
    activation_consent: bool = False,
    phone: str = "",
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
        raise ValidationError("اطلاعات ثبت سفارش درست نیست.")
    allowed = {str(line[0]) for line in payload["inputs"]}
    if set(customer_inputs) - allowed or type(activation_consent) is not bool:
        raise ValidationError("اطلاعات ثبت سفارش درست نیست.")
    inputs = {
        str(plan_id): normalize_customer_input(kind, customer_inputs.get(str(plan_id)))
        for plan_id, kind in payload["inputs"]
    }
    phone = normalize_mobile_phone(phone)
    submission = _json(
        {
            "quote": payload,
            "inputs": inputs,
            "phone": phone,
            "consent": activation_consent,
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
        phone=phone,
        discount_code=payload["code"],
        activation_consent=activation_consent,
        checkout_id=checkout_id,
        submission_digest=digest,
        expected_terms=payload["terms"],
    )
    _check_quote_age(quote)
    cart.items.all().delete()
    Cart.objects.filter(pk=cart.pk).update(updated_at=timezone.now())
    return order

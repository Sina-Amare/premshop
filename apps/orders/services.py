"""Locked purchase facts and whole-order resource release."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from apps.accounts.models import User
from apps.accounts.validators import normalize_mobile_phone
from apps.catalog.inputs import normalize_customer_input
from apps.catalog.models import Plan, Product
from apps.catalog.pricing import effective_price
from apps.orders.models import DiscountCode, DiscountRedemption, Order, OrderItem, OrderItemEvent

CONSENT_VERSION = "2026-10-29"
CONSENT_TEXT = "درخواست می‌کنم فعال‌سازی سفارش پس از تأیید پرداخت آغاز شود."
MAX_AMOUNT = Decimal("999999999999")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderLine:
    plan: Plan
    quantity: int
    customer_input: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PricedLine:
    plan: Plan
    quantity: int
    customer_input: str = field(repr=False)
    price: Decimal
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class Purchase:
    lines: tuple[PricedLine, ...]
    subtotal: Decimal
    code: DiscountCode | None
    discount: Decimal
    discount_snapshot: dict[str, Any]

    @property
    def total(self) -> Decimal:
        return self.subtotal - self.discount

    def terms(self) -> dict[str, Any]:
        return {
            "lines": [
                {
                    "plan": line.plan.pk,
                    "quantity": line.quantity,
                    "price": str(line.price),
                    "facts": line.snapshot,
                    "max_quantity": line.plan.max_quantity,
                }
                for line in self.lines
            ],
            "subtotal": str(self.subtotal),
            "discount": str(self.discount),
            "discount_terms": self.discount_snapshot,
            "total": str(self.total),
            "consent_version": CONSENT_VERSION,
            "consent_text": CONSENT_TEXT,
        }

    def terms_digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.terms(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()


class UnpaidOrderExists(ValidationError):
    def __init__(self, order: Order) -> None:
        self.order = order
        super().__init__("تا زمانی که سفارش فعلی منتظر پرداخت است، نمی‌توانید سفارش جدیدی ثبت کنید.")


def _money(value: Decimal) -> Decimal:
    if (
        not value.is_finite()
        or value < 0
        or value > MAX_AMOUNT
        or value != value.to_integral_value()
    ):
        raise ValidationError("مبلغ سفارش خارج از محدوده‌ی مجاز است.")
    return value


def _snapshot(plan: Plan) -> dict[str, Any]:
    product = plan.product
    return {
        "name": product.name,
        "plan_title": plan.title,
        "category_id": product.category_id,
        "category_name": product.category.name,
        "warranty": product.warranty,
        "delivery_type": product.delivery_type,
        "delivery_hours": product.delivery_hours,
        "delivery_note": product.delivery_note,
        "duration_days": plan.duration_days,
        "usage_notes": product.usage_notes,
        "refund_note": product.refund_note,
        "requires_customer_input": plan.requires_customer_input,
        "input_type": plan.input_type,
        "telegram_required": plan.telegram_required,
        "customer_input_label": plan.activation_input_label,
        "specs": [{"title": spec.title, "value": spec.value} for spec in product.specs.all()],
    }


def _code_name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 32:
        raise ValidationError("کد تخفیف معتبر نیست.")
    value = value.strip().upper()
    if value and not re.fullmatch(r"[A-Z0-9]{4,8}", value):
        raise ValidationError("کد تخفیف معتبر نیست.")
    return value


def _discount(
    code: DiscountCode | None, user_id: int, lines: Sequence[PricedLine], at: datetime
) -> tuple[Decimal, dict[str, Any]]:
    if code is None:
        return Decimal(0), {}
    if not code.is_active:
        raise ValidationError("این کد تخفیف فعال نیست.")
    if code.valid_from and at < code.valid_from:
        raise ValidationError("زمان استفاده از این کد تخفیف هنوز نرسیده است.")
    if code.valid_until and at >= code.valid_until:
        raise ValidationError("مهلت استفاده از این کد تخفیف تمام شده است.")
    if code.max_uses is not None and code.used_count >= code.max_uses:
        raise ValidationError("ظرفیت استفاده از این کد تخفیف تمام شده است.")
    uses = DiscountRedemption.objects.filter(
        discount_code=code, user_id=user_id, released_at__isnull=True
    )
    if code.per_user_limit is not None and uses.count() >= code.per_user_limit:
        raise ValidationError("شما قبلاً از ظرفیت مجاز این کد تخفیف استفاده کرده‌اید.")
    subtotal = sum((line.price * line.quantity for line in lines), Decimal(0))
    if code.min_order_amount is not None and subtotal < code.min_order_amount:
        raise ValidationError("مبلغ سفارش به حداقل لازم برای این کد تخفیف نمی‌رسد.")
    product_ids = (
        sorted(code.products.values_list("pk", flat=True))
        if code.scope == DiscountCode.Scope.SELECTED
        else []
    )
    eligible = sum(
        (
            line.price * line.quantity
            for line in lines
            if code.scope == DiscountCode.Scope.ALL or line.plan.product_id in product_ids
        ),
        Decimal(0),
    )
    if eligible <= 0:
        raise ValidationError("این کد تخفیف برای محصولات انتخاب‌شده قابل استفاده نیست.")
    amount = (
        (eligible * code.value / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        if code.kind == DiscountCode.Kind.PERCENT
        else code.value
    )
    return min(amount, eligible), {
        "code": code.code,
        "kind": code.kind,
        "value": str(code.value),
        "scope": code.scope,
        "products": product_ids,
        "minimum": str(code.min_order_amount) if code.min_order_amount is not None else None,
    }


def _price(
    *,
    user_id: int,
    lines: Sequence[OrderLine],
    plans: dict[int, Plan],
    code: DiscountCode | None,
    at: datetime,
) -> Purchase:
    if not lines or len(lines) > 100:
        raise ValidationError("اطلاعات سفارش درست نیست.")
    ids = [line.plan.pk for line in lines]
    if len(set(ids)) != len(ids):
        raise ValidationError("هر پلن را فقط یک بار به سفارش اضافه کنید.")
    prepared = []
    subtotal = Decimal(0)
    for line in sorted(lines, key=lambda line: line.plan.pk or 0):
        plan = plans.get(line.plan.pk or 0)
        if plan is None or not plan.is_available or not plan.product.is_public:
            raise ValidationError("این پلن الان قابل خرید نیست.")
        plan.validate_input_configuration()
        if type(line.quantity) is not int or not 1 <= line.quantity <= plan.max_quantity:
            raise ValidationError("تعداد انتخاب‌شده برای این پلن مجاز نیست.")
        if plan.stock_quantity is not None and plan.stock_quantity < line.quantity:
            raise ValidationError("موجودی این محصول برای تعداد انتخاب‌شده کافی نیست.")
        value = "" if line.customer_input is None else line.customer_input
        if not isinstance(value, str) or len(value) > 2000:
            raise ValidationError("اطلاعات فعال‌سازی درست نیست.")
        price = _money(effective_price(plan, at=at))
        _money(plan.cost_price)
        subtotal = _money(subtotal + price * line.quantity)
        prepared.append(PricedLine(plan, line.quantity, value.strip(), price, _snapshot(plan)))
    discount, snapshot = _discount(code, user_id, prepared, at)
    purchase = Purchase(tuple(prepared), subtotal, code, _money(discount), snapshot)
    if purchase.total == 0:
        raise ValidationError("ثبت سفارش با مبلغ نهایی صفر امکان‌پذیر نیست.")
    return purchase


def _plans(ids: Sequence[int], *, lock: bool) -> dict[int, Plan]:
    queryset = Plan.objects.filter(pk__in=ids)
    product_ids = sorted(set(queryset.values_list("product_id", flat=True)))
    if lock:
        list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by("pk"))
        queryset = queryset.select_for_update(of=("self",))
    plans = list(
        queryset.select_related("product", "product__category")
        .prefetch_related("product__specs")
        .order_by("pk")
    )
    if any(plan.product_id not in product_ids for plan in plans):
        raise ValidationError("اطلاعات محصول تغییر کرده است. صفحه را دوباره باز کنید.")
    return {plan.pk: plan for plan in plans}


def quote_order(*, user: User, lines: Sequence[OrderLine], discount_code: str = "") -> Purchase:
    name = _code_name(discount_code)
    code = DiscountCode.objects.filter(code=name).first() if name else None
    if name and code is None:
        raise ValidationError("کد تخفیف معتبر نیست.")
    return _price(
        user_id=user.pk,
        lines=lines,
        plans=_plans([line.plan.pk for line in lines], lock=False),
        code=code,
        at=timezone.now(),
    )


def replay_checkout(*, user: User, checkout_id: uuid.UUID, submission_digest: str) -> Order | None:
    order = Order.objects.filter(checkout_id=checkout_id).first()
    if order is not None and (
        order.user_id != user.pk
        or not constant_time_compare(order.submission_digest, submission_digest)
    ):
        raise ValidationError("شناسه‌ی ثبت سفارش با این درخواست مطابقت ندارد.")
    return order


@transaction.atomic
def place_order(
    *,
    user: User,
    lines: Sequence[OrderLine],
    phone: str = "",
    channel: str = Order.Channel.WEB,
    discount_code: str = "",
    activation_consent: bool = False,
    checkout_id: uuid.UUID | None = None,
    submission_digest: str = "",
    expected_terms: dict[str, Any] | str | None = None,
) -> Order:
    account = (
        User.objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).first()
        if user.pk
        else None
    )
    if account is None:
        raise PermissionDenied
    if checkout_id is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", submission_digest):
            raise ValidationError("اطلاعات ثبت سفارش درست نیست.")
        if replay := replay_checkout(
            user=account, checkout_id=checkout_id, submission_digest=submission_digest
        ):
            return replay
    if activation_consent is not True:
        raise ValidationError("شروع فعال‌سازی پس از تأیید پرداخت را تأیید کنید.")
    if (
        channel not in (Order.Channel.WEB, Order.Channel.BOT)
        or not lines
        or len(lines) > 100
        or any(type(line.plan.pk) is not int for line in lines)
    ):
        raise ValidationError("اطلاعات سفارش درست نیست.")
    phone = normalize_mobile_phone(phone)
    name = _code_name(discount_code)
    requested_code_id = (
        DiscountCode.objects.filter(code=name).values_list("pk", flat=True).first()
        if name
        else None
    )
    if name and requested_code_id is None:
        raise ValidationError("کد تخفیف معتبر نیست.")
    pending = OrderItem.objects.filter(
        order_id=OuterRef("pk"), status=OrderItem.Status.PENDING_PAYMENT
    )
    existing = list(
        Order.objects.select_for_update(of=("self",))
        .filter(user=account)
        .filter(Exists(pending))
        .order_by("pk")
    )
    existing_ids = [order.pk for order in existing]
    plan_ids = set(
        OrderItem.objects.filter(order_id__in=existing_ids).values_list("plan_id", flat=True)
    )
    plan_ids.update(line.plan.pk for line in lines)
    plans = _plans(sorted(plan_ids), lock=True)
    code_ids = {order.discount_code_id for order in existing if order.discount_code_id}
    if requested_code_id is not None:
        code_ids.add(requested_code_id)
    codes = {
        code.pk: code
        for code in DiscountCode.objects.select_for_update().filter(pk__in=code_ids).order_by("pk")
    }
    items = list(
        OrderItem.objects.select_for_update()
        .filter(order_id__in=existing_ids)
        .defer("customer_input")
        .order_by("pk")
    )
    at = timezone.now()
    for order in existing:
        if order.unpaid_expires_at <= at:
            _release(
                order,
                [item for item in items if item.order_id == order.pk],
                plans,
                codes.get(order.discount_code_id) if order.discount_code_id else None,
                at=at,
                reason=OrderItem.CancelReason.EXPIRED_UNPAID,
            )
        else:
            raise UnpaidOrderExists(order)
    code = codes.get(requested_code_id) if requested_code_id is not None else None
    if name and (code is None or code.code != name):
        raise ValidationError("کد تخفیف معتبر نیست.")
    purchase = _price(user_id=account.pk, lines=lines, plans=plans, code=code, at=at)
    current_terms = purchase.terms_digest() if isinstance(expected_terms, str) else purchase.terms()
    if expected_terms is not None and current_terms != expected_terms:
        raise ValidationError(
            "قیمت یا شرایط خرید تغییر کرده است. خلاصه‌ی جدید را بررسی و دوباره تأیید کنید.",
            code="quote_changed",
        )
    inputs = {
        line.plan.pk: normalize_customer_input(line.plan.input_type, line.customer_input)
        for line in purchase.lines
    }
    try:
        with transaction.atomic():
            order = Order.objects.create(
                user=account,
                subtotal=purchase.subtotal,
                total_amount=purchase.total,
                discount_code=code,
                discount_amount=purchase.discount,
                discount_snapshot=purchase.discount_snapshot,
                channel=channel,
                created_at=at,
                unpaid_expires_at=at + timedelta(hours=24),
                consent_text=CONSENT_TEXT,
                consent_version=CONSENT_VERSION,
                consent_accepted_at=at,
                **(
                    {"checkout_id": checkout_id, "submission_digest": submission_digest}
                    if checkout_id
                    else {}
                ),
            )
    except IntegrityError:
        if checkout_id and (
            replay := replay_checkout(
                user=account, checkout_id=checkout_id, submission_digest=submission_digest
            )
        ):
            return replay
        raise
    for line in purchase.lines:
        debited = line.plan.stock_quantity is not None
        if line.plan.stock_quantity is not None:
            line.plan.stock_quantity -= line.quantity
            Plan.objects.filter(pk=line.plan.pk).update(stock_quantity=line.plan.stock_quantity)
        for _ in range(line.quantity):
            item = OrderItem.objects.create(
                order=order,
                plan=line.plan,
                price_snapshot=line.price,
                cost_snapshot=line.plan.cost_price,
                product_snapshot=line.snapshot,
                customer_input=inputs[line.plan.pk],
                stock_debited=debited,
            )
            OrderItemEvent.objects.create(
                order_item=item,
                from_status=None,
                to_status=item.status,
                actor=OrderItemEvent.Actor.CUSTOMER,
                actor_user=account,
            )
    if code is not None:
        DiscountRedemption.objects.create(
            discount_code=code, user=account, order=order, amount=purchase.discount
        )
        code.used_count += 1
        code.save(update_fields=["used_count"])
    if phone and not account.phone:
        User.objects.filter(pk=account.pk).update(phone=phone)
    return order


def _release(
    order: Order,
    items: Sequence[OrderItem],
    plans: dict[int, Plan],
    code: DiscountCode | None,
    *,
    at: datetime,
    reason: str,
    actor: User | None = None,
    note: str = "",
) -> bool:
    if items and all(item.status == OrderItem.Status.CANCELLED for item in items):
        return False
    if not items or any(
        item.status not in (OrderItem.Status.PENDING_PAYMENT, OrderItem.Status.CANCELLED)
        for item in items
    ):
        raise ValidationError("فقط سفارش منتظر پرداخت از این مسیر لغو می‌شود.")
    # Legacy orders may already contain individually cancelled units.
    pending = [item for item in items if item.status == OrderItem.Status.PENDING_PAYMENT]
    quantities = Counter(item.plan_id for item in pending if item.stock_debited)
    for plan_id in sorted(quantities):
        plan = plans[plan_id]
        if plan.stock_quantity is not None:
            quantity = plan.stock_quantity + quantities[plan_id]
            if quantity > 2147483647:
                raise ValidationError(
                    "موجودی ثبت‌شده بیش از حد مجاز است. پشتیبانی باید موجودی را بررسی کند."
                )
            plan.stock_quantity = quantity
            Plan.objects.filter(pk=plan_id).update(stock_quantity=quantity)
    redemption = DiscountRedemption.objects.filter(order=order, released_at__isnull=True).first()
    if redemption is not None:
        if code is None or code.used_count < 1:
            raise ValidationError("شمارنده‌ی کد تخفیف به بررسی پشتیبانی نیاز دارد.")
        redemption.released_at = at
        redemption.save(update_fields=["released_at"])
        code.used_count -= 1
        code.save(update_fields=["used_count"])
    for item in pending:
        item.status, item.cancel_reason = OrderItem.Status.CANCELLED, reason
        item.save(update_fields=["status", "cancel_reason"])
        OrderItemEvent.objects.create(
            order_item=item,
            from_status=OrderItem.Status.PENDING_PAYMENT,
            to_status=item.status,
            actor=(
                OrderItemEvent.Actor.SYSTEM
                if actor is None
                else (
                    OrderItemEvent.Actor.OPERATOR
                    if reason == OrderItem.CancelReason.OPERATOR
                    else OrderItemEvent.Actor.CUSTOMER
                )
            ),
            actor_user=actor,
            note=note if reason == OrderItem.CancelReason.OPERATOR else "",
        )
    order.cancelled_at = at
    order.save(update_fields=["cancelled_at"])
    return True


@transaction.atomic
def _cancel(
    *, order_id: int, actor: User | None = None, note: str = "", due_at: datetime | None = None
) -> Order | None:
    if not isinstance(note, str) or len(note) > 2000:
        raise ValidationError("دلیل لغو معتبر نیست.")
    account = User.objects.filter(pk=actor.pk, is_active=True).first() if actor else None
    if actor and account is None:
        raise PermissionDenied
    query = Order.objects.filter(pk=order_id)
    if account and not account.is_staff:
        query = query.filter(user_id=account.pk)
    owner_id = query.values_list("user_id", flat=True).first()
    if owner_id is None:
        raise PermissionDenied
    User.objects.select_for_update(no_key=True).get(pk=owner_id)
    order = query.select_for_update(of=("self",)).get()
    if due_at is not None and order.unpaid_expires_at > due_at:
        return None
    operator = (
        account is not None and account.is_staff and (account.pk != owner_id or bool(note.strip()))
    )
    if operator and not note.strip():
        raise ValidationError("دلیل لغو را بنویسید.")
    plans = _plans(list(order.items.values_list("plan_id", flat=True)), lock=True)
    code = (
        DiscountCode.objects.select_for_update().filter(pk=order.discount_code_id).first()
        if order.discount_code_id
        else None
    )
    items = list(order.items.select_for_update().defer("customer_input").order_by("pk"))
    if due_at is not None:
        if not any(item.status == OrderItem.Status.PENDING_PAYMENT for item in items) or any(
            item.status not in (OrderItem.Status.PENDING_PAYMENT, OrderItem.Status.CANCELLED)
            for item in items
        ):
            return None
    _release(
        order,
        items,
        plans,
        code,
        at=timezone.now(),
        actor=account,
        note=note,
        reason=(
            OrderItem.CancelReason.EXPIRED_UNPAID
            if due_at is not None
            else (
                OrderItem.CancelReason.OPERATOR
                if operator
                else OrderItem.CancelReason.CUSTOMER_BEFORE_PAYMENT
            )
        ),
    )
    return order


def cancel_unpaid_order(*, order_id: int, actor: User, note: str = "") -> Order:
    return cast(Order, _cancel(order_id=order_id, actor=actor, note=note))


def cancel_unpaid_item(*, item_id: int, actor: User, note: str = "") -> OrderItem:
    item = OrderItem.objects.only("pk", "order_id").get(pk=item_id)
    cancel_unpaid_order(order_id=item.order_id, actor=actor, note=note)
    return OrderItem.objects.defer("customer_input").get(pk=item_id)


def expire_unpaid_orders(*, limit: int = 100, user_id: int | None = None) -> int:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Expiry batches must hold between 1 and 1000 orders.")
    at = timezone.now()
    pending = OrderItem.objects.filter(
        order_id=OuterRef("pk"), status=OrderItem.Status.PENDING_PAYMENT
    )
    orders = Order.objects.filter(unpaid_expires_at__lte=at)
    if user_id is not None:
        orders = orders.filter(user_id=user_id)
    ids = list(
        orders.filter(Exists(pending))
        .order_by(F("expiry_last_attempt_at").asc(nulls_first=True), "unpaid_expires_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    processed = 0
    for order_id in ids:
        try:
            if _cancel(order_id=order_id, due_at=at) is not None:
                processed += 1
                continue
        except ValidationError as error:
            logger.error(
                "Unpaid expiry needs review for order %s: %s", order_id, type(error).__name__
            )
        # Failed rows rotate behind unattempted rows, then receive another turn.
        Order.objects.filter(pk=order_id).update(expiry_last_attempt_at=at)
    return processed

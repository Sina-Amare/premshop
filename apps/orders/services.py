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
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from ipaddress import ip_address
from typing import Any, cast

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, TextField
from django.db.models.functions import Cast, Length
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.translation import gettext_lazy

from apps.accounts.models import User
from apps.accounts.validators import normalize_mobile_phone
from apps.catalog.inputs import normalize_customer_input
from apps.catalog.models import Plan, Product, ProductSection
from apps.catalog.pricing import effective_price
from apps.cms.models import Page
from apps.core.formatting import to_latin_digits
from apps.orders.clock import delivery_deadline
from apps.orders.models import (
    SUPPORT_ALPHABET,
    CredentialAccessLog,
    Delivery,
    DeliveryField,
    DiscountCode,
    DiscountRedemption,
    Order,
    OrderItem,
    OrderItemEvent,
    OrderSupportIdentifier,
)
from apps.orders.models import (
    support_token as new_support_token,
)

MAX_AMOUNT = Decimal("999999999999")
logger = logging.getLogger(__name__)


def match_support_token(*, order_id: int, actor: User, support_token: str) -> bool:
    if not (
        actor.is_active
        and actor.is_staff
        and (actor.has_perm("orders.view_order") or actor.has_perm("orders.change_order"))
    ):
        raise PermissionDenied
    identifier = OrderSupportIdentifier.objects.get(order_id=order_id)
    if len(support_token) > 64:
        return False
    support_token = re.sub(r"[\s-]", "", to_latin_digits(support_token)).upper()
    return bool(
        re.fullmatch(f"[{SUPPORT_ALPHABET}]{{12}}", support_token)
    ) and constant_time_compare(identifier.support_token, support_token)


@transaction.atomic
def replace_support_token(
    *, order_id: int, actor: User, expected_support_token: str
) -> OrderSupportIdentifier:
    if not actor.is_active or not actor.is_authenticated:
        raise PermissionDenied
    order = Order.objects.select_for_update().filter(pk=order_id, user_id=actor.pk).first()
    if order is None:
        raise PermissionDenied
    identifier = OrderSupportIdentifier.objects.get(order_id=order.pk)
    if not constant_time_compare(identifier.support_token, expected_support_token):
        raise ValidationError("شناسه تغییر کرده است؛ صفحه را دوباره باز کنید.")
    while constant_time_compare(identifier.support_token, expected_support_token):
        identifier.support_token = new_support_token()
    identifier.save(update_fields=["support_token"])
    return identifier


def _may_reveal(actor: User) -> None:
    if not (
        actor.is_active
        and actor.is_staff
        and (actor.has_perm("orders.view_order") or actor.has_perm("orders.change_order"))
        and (actor.has_perm("orders.view_orderitem") or actor.has_perm("orders.change_orderitem"))
    ):
        raise PermissionDenied


def _log_reveal(item_id: int, actor: User, ip: str | None, target: str) -> None:
    try:
        address = str(ip_address(ip)) if ip else None
    except ValueError:
        address = None
    CredentialAccessLog.objects.create(order_item_id=item_id, user=actor, ip=address, target=target)


def reveal_customer_input(*, order_id: int, item_id: int, actor: User, ip: str | None) -> str:
    with transaction.atomic(durable=True):
        _may_reveal(actor)
        item = OrderItem.objects.only("customer_input").get(pk=item_id, order_id=order_id)
        value = item.customer_input
        if value:
            _log_reveal(item_id, actor, ip, CredentialAccessLog.Target.CUSTOMER_INPUT)
    return value


DELIVERY_LINES = 6


@dataclass(frozen=True)
class DeliveryLine:
    title: str
    value: str = field(repr=False)


@dataclass(frozen=True)
class DeliveryContent:
    lines: tuple[DeliveryLine, ...]
    note: str = field(repr=False)
    expires_at: date | None = None


def delivery_content(
    lines: Sequence[DeliveryLine], note: str, expires_at: date | None
) -> DeliveryContent:
    """The one reading of a delivery, shared by its preview and the stored set."""
    kept = tuple(
        DeliveryLine(line.title.strip(), line.value.strip())
        for line in lines
        if line.title.strip() or line.value.strip()
    )
    if not kept:
        raise ValidationError(gettext_lazy("دست‌کم یک سطر اطلاعات تحویل بنویسید."))
    if any(not line.title or not line.value for line in kept):
        raise ValidationError(gettext_lazy("هر سطر تحویل هم عنوان می‌خواهد هم مقدار."))
    if (
        len(kept) > DELIVERY_LINES
        or len(note) > 2000
        or any(len(line.title) > 100 or len(line.value) > 2000 for line in kept)
    ):
        raise ValidationError(gettext_lazy("اطلاعات تحویل بیش از اندازه طولانی است."))
    if expires_at is not None and expires_at < timezone.localdate():
        raise ValidationError(gettext_lazy("تاریخ پایان اشتراک گذشته است."))
    return DeliveryContent(kept, note.strip(), expires_at)


@transaction.atomic
def deliver_item(
    *,
    order_id: int,
    item_id: int,
    actor: User,
    expected_event_id: int,
    lines: Sequence[DeliveryLine],
    note: str,
    expires_at: date | None,
    actual_cost: Decimal,
) -> Delivery:
    item, account = _input_item(order_id=order_id, item_id=item_id, actor=actor, operator_only=True)
    content = delivery_content(lines, note, expires_at)
    if not (
        isinstance(actual_cost, Decimal)
        and actual_cost.is_finite()
        and actual_cost == actual_cost.to_integral_value()
        and 0 <= actual_cost <= MAX_AMOUNT
    ):
        raise ValidationError(gettext_lazy("قیمت تأمین واقعی معتبر نیست."))
    latest = item.events.order_by("-pk").first()
    if latest is None or latest.pk != expected_event_id:
        previous = item.events.filter(pk__lt=latest.pk if latest else 0).order_by("-pk").first()
        # This operator confirmed from the same page twice (a double click or a second
        # tab): the first set stands, and this one is not stored, so it is never success.
        if (
            latest is not None
            and previous is not None
            and previous.pk == expected_event_id
            and (latest.from_status, latest.to_status, latest.actor_user_id)
            == (OrderItem.Status.QUEUED, OrderItem.Status.DELIVERED, account.pk)
        ):
            raise ValidationError(
                gettext_lazy(
                    "این محصول قبلاً تحویل شده و این ارسال ذخیره نشد. اطلاعات ثبت‌شده را با"
                    " دکمه‌ی «نمایش اطلاعات تحویل» در صفحه‌ی سفارش ببینید."
                )
            )
        raise ValidationError(
            gettext_lazy("وضعیت این محصول تغییر کرده است؛ صفحه را دوباره باز کنید.")
        )
    if item.status != OrderItem.Status.QUEUED or item.paid_at is None or item.sla_paused_at:
        raise ValidationError(gettext_lazy("این محصول آماده‌ی تحویل نیست."))
    item.status, item.delivered_at = OrderItem.Status.DELIVERED, timezone.now()
    item.expires_at, item.actual_cost = content.expires_at, actual_cost
    item.save(update_fields=["status", "delivered_at", "expires_at", "actual_cost"])
    delivery = Delivery.objects.create(
        order_item=item,
        event=_input_event(item, account, OrderItem.Status.QUEUED),
        note=content.note,
    )
    DeliveryField.objects.bulk_create(
        DeliveryField(delivery=delivery, position=n, title=line.title, value=line.value)
        for n, line in enumerate(content.lines)
    )
    return delivery


def reveal_delivery(
    *, order_id: int, item_id: int, actor: User, ip: str | None
) -> DeliveryContent | None:
    """The current set, decrypted only after its access row is written; None before delivery."""
    with transaction.atomic(durable=True):
        _may_reveal(actor)
        item = OrderItem.objects.only("pk", "expires_at").get(pk=item_id, order_id=order_id)
        current = (
            Delivery.objects.filter(order_item_id=item.pk)
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first()
        )
        if current is None:
            return None
        _log_reveal(item.pk, actor, ip, CredentialAccessLog.Target.DELIVERY)
        delivery = Delivery.objects.get(pk=current)
        content = DeliveryContent(
            tuple(DeliveryLine(row.title, row.value) for row in delivery.fields.all()),
            delivery.note,
            item.expires_at,
        )
    return content


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

    @property
    def amount(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True)
class Purchase:
    lines: tuple[PricedLine, ...]
    subtotal: Decimal
    code: DiscountCode | None
    discount: Decimal
    discount_snapshot: dict[str, Any]
    policy_snapshot: dict[str, Any]

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
            "policies": self.policy_snapshot,
            "total": str(self.total),
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
        super().__init__(
            "تا زمانی که سفارش فعلی منتظر پرداخت است، نمی‌توانید سفارش جدیدی ثبت کنید."
        )


def _money(value: Decimal) -> Decimal:
    if (
        not value.is_finite()
        or value < 0
        or value > MAX_AMOUNT
        or value != value.to_integral_value()
    ):
        raise ValidationError("مبلغ این سفارش از حد مجاز بیشتر است.")
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
        "full_description": product.full_description,
        "sections": [
            {
                "kind": section.kind,
                "title": section.title
                or (
                    ""
                    if section.kind == ProductSection.Kind.FREE
                    else str(section.get_kind_display())
                ),
                "body": section.body,
            }
            for section in product.sections.all()
        ],
    }


def _policy_snapshot() -> dict[str, Any]:
    pages = Page.objects.filter(
        slug__in=[Page.Slug.TERMS, Page.Slug.REFUNDS, Page.Slug.CONTACT], is_published=True
    ).values("slug", "title", "body")
    snapshot = {}
    for page in pages:
        facts = {"title": page["title"], "body": page["body"]}
        content = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        snapshot[page["slug"]] = {
            **facts,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }
    return snapshot


def _code_name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 32:
        raise ValidationError("این کد تخفیف درست نیست.")
    value = value.strip().upper()
    if value and not re.fullmatch(r"[A-Z0-9]{4,8}", value):
        raise ValidationError("این کد تخفیف درست نیست.")
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
        raise ValidationError("ظرفیت این کد تخفیف تمام شده است.")
    uses = DiscountRedemption.objects.filter(
        discount_code=code, user_id=user_id, released_at__isnull=True
    )
    if code.per_user_limit is not None and uses.count() >= code.per_user_limit:
        raise ValidationError("شما قبلاً به تعداد مجاز از این کد تخفیف استفاده کرده‌اید.")
    subtotal = sum((line.price * line.quantity for line in lines), Decimal(0))
    if code.min_order_amount is not None and subtotal < code.min_order_amount:
        raise ValidationError("مبلغ سفارش برای استفاده از این کد تخفیف کافی نیست.")
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
        raise ValidationError("این کد تخفیف برای این محصولات کار نمی‌کند.")
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
        raise ValidationError("هر گزینه را فقط یک بار به سفارش اضافه کنید.")
    prepared = []
    subtotal = Decimal(0)
    for line in sorted(lines, key=lambda line: line.plan.pk or 0):
        plan = plans.get(line.plan.pk or 0)
        if plan is None or not plan.is_available or not plan.product.is_public:
            raise ValidationError("این گزینه فعلاً موجود نیست.")
        plan.validate_input_configuration()
        if type(line.quantity) is not int or not 1 <= line.quantity <= plan.max_quantity:
            raise ValidationError("از این گزینه نمی‌توانید این تعداد بخرید.")
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
    purchase = Purchase(
        tuple(prepared), subtotal, code, _money(discount), snapshot, _policy_snapshot()
    )
    if purchase.total == 0:
        raise ValidationError("سفارش با مبلغ نهایی صفر ثبت نمی‌شود.")
    return purchase


def _plans(ids: Sequence[int], *, lock: bool) -> dict[int, Plan]:
    queryset = Plan.objects.filter(pk__in=ids)
    product_ids = sorted(set(queryset.values_list("product_id", flat=True)))
    if lock:
        list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by("pk"))
        queryset = queryset.select_for_update(of=("self",))
    plans = list(
        queryset.select_related("product", "product__category")
        .prefetch_related("product__specs", "product__sections")
        .order_by("pk")
    )
    if any(plan.product_id not in product_ids for plan in plans):
        raise ValidationError("اطلاعات محصول تغییر کرده است. صفحه را دوباره باز کنید.")
    return {plan.pk: plan for plan in plans}


def quote_order(*, user: User, lines: Sequence[OrderLine], discount_code: str = "") -> Purchase:
    name = _code_name(discount_code)
    code = DiscountCode.objects.filter(code=name).first() if name else None
    if name and code is None:
        raise ValidationError("این کد تخفیف درست نیست.")
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
        raise ValidationError(
            "این فرم قبلاً با اطلاعات دیگری فرستاده شده است. صفحه را دوباره باز کنید."
        )
    return order


@transaction.atomic
def place_order(
    *,
    user: User,
    lines: Sequence[OrderLine],
    phone: str = "",
    channel: str = Order.Channel.WEB,
    discount_code: str = "",
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
        raise ValidationError("این کد تخفیف درست نیست.")
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
        raise ValidationError("این کد تخفیف درست نیست.")
    purchase = _price(user_id=account.pk, lines=lines, plans=plans, code=code, at=at)
    current_terms = purchase.terms_digest() if isinstance(expected_terms, str) else purchase.terms()
    if expected_terms is not None and current_terms != expected_terms:
        raise ValidationError(
            "قیمت یا شرایط خرید تغییر کرده است. تغییرات را بررسی کنید و دوباره «ثبت سفارش» را بزنید.",
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
                policy_snapshot=purchase.policy_snapshot,
                placement_email=account.email,
                channel=channel,
                created_at=at,
                unpaid_expires_at=at + timedelta(hours=24),
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
    OrderSupportIdentifier.objects.create(order=order)
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
                input_received_at=(
                    at
                    if line.snapshot["requires_customer_input"]
                    and not line.snapshot["telegram_required"]
                    else None
                ),
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


def can_pay_order(
    order: Order,
    *,
    items: Sequence[OrderItem] | None = None,
    payment_id: int | None = None,
) -> bool:
    if order.cancelled_at or order.unpaid_expires_at <= timezone.now() or order.total_amount <= 0:
        return False
    payments = order.payments.filter(status__in=["created", "initiated", "verified"])
    if payment_id is not None:
        payments = payments.exclude(pk=payment_id)
    if payments.exists():
        return False
    if items is None:
        items = list(order.items.defer("customer_input"))
    if not items:
        return False
    for item in items:
        facts = item.product_snapshot
        if (
            item.status != OrderItem.Status.PENDING_PAYMENT
            or type(facts.get("telegram_required")) is not bool
            or facts.get("input_type") not in ("none", "email", "username")
            or facts.get("requires_customer_input") is not (facts.get("input_type") != "none")
            or (
                facts.get("requires_customer_input")
                and not facts["telegram_required"]
                and item.input_received_at is None
            )
            or type(facts.get("delivery_hours")) is not int
            or not 1 <= facts["delivery_hours"] <= 48
        ):
            return False
    # Eligibility checks presence without decrypting input on the order page.
    return not (
        OrderItem.objects.filter(
            pk__in=[item.pk for item in items if item.product_snapshot["requires_customer_input"]]
        )
        .annotate(input_length=Length(Cast("customer_input", TextField())))
        .filter(input_length=0)
        .exists()
    )


@transaction.atomic
def mark_order_paid(*, order: Order, payment: Any, actor: User) -> None:
    if not (
        actor.is_active
        and actor.is_staff
        and actor.has_perms(["orders.change_order", "orders.change_orderitem"])
        and payment.order_id == order.pk
        and payment.status == "verified"
        and payment.amount == order.total_amount
        and payment.verified_at is not None
    ):
        raise PermissionDenied
    User.objects.select_for_update(no_key=True).get(pk=order.user_id)
    order = Order.objects.select_for_update().get(pk=order.pk)
    items = list(order.items.select_for_update().defer("customer_input").order_by("pk"))
    if not can_pay_order(order, items=items, payment_id=payment.pk):
        raise ValidationError(gettext_lazy("وضعیت این سفارش برای ثبت پرداخت مناسب نیست."))
    for item in items:
        item.status, item.paid_at = OrderItem.Status.QUEUED, payment.verified_at
        item.due_at = delivery_deadline(
            paid_at=item.paid_at,
            delivery_hours=item.product_snapshot["delivery_hours"],
            requires_input=item.clock_requires_input,
            input_received_at=item.input_received_at,
        )
        item.save(update_fields=["status", "paid_at", "due_at"])
        event = OrderItemEvent.objects.create(
            order_item=item,
            from_status=OrderItem.Status.PENDING_PAYMENT,
            to_status=item.status,
            actor=OrderItemEvent.Actor.OPERATOR,
            actor_user=actor,
        )
        if item.product_snapshot["telegram_required"]:
            request_input(
                order_id=order.pk, item_id=item.pk, actor=actor, expected_event_id=event.pk
            )


def _input_item(
    *, order_id: int, item_id: int, actor: User, operator_only: bool
) -> tuple[OrderItem, User]:
    account = User.objects.filter(pk=actor.pk, is_active=True).first()
    if account is None or (
        (account.is_staff or operator_only)
        and not (
            account.is_staff
            and account.has_perms(["orders.change_order", "orders.change_orderitem"])
        )
    ):
        raise PermissionDenied
    orders = Order.objects.filter(pk=order_id)
    if not account.is_staff:
        orders = orders.filter(user_id=account.pk)
    owner_id = orders.values_list("user_id", flat=True).first()
    if owner_id is None:
        raise PermissionDenied
    User.objects.select_for_update(no_key=True).get(pk=owner_id)
    order = orders.select_for_update().get()
    item = order.items.select_for_update().defer("customer_input").filter(pk=item_id).first()
    if item is None:
        raise PermissionDenied
    return item, account


def _input_replay(item: OrderItem, expected_event_id: int, before: str, after: str) -> bool:
    latest = item.events.order_by("-pk").first()
    if latest is not None and latest.pk == expected_event_id:
        return False
    if latest is not None and (latest.from_status, latest.to_status) == (before, after):
        previous = item.events.filter(pk__lt=latest.pk).order_by("-pk").first()
        if previous is not None and previous.pk == expected_event_id:
            return True
    raise ValidationError(gettext_lazy("وضعیت این محصول تغییر کرده است؛ صفحه را دوباره باز کنید."))


def _input_event(item: OrderItem, account: User, before: str) -> OrderItemEvent:
    return OrderItemEvent.objects.create(
        order_item=item,
        from_status=before,
        to_status=item.status,
        actor=OrderItemEvent.Actor.OPERATOR if account.is_staff else OrderItemEvent.Actor.CUSTOMER,
        actor_user=account,
    )


@transaction.atomic
def request_input(*, order_id: int, item_id: int, actor: User, expected_event_id: int) -> OrderItem:
    item, account = _input_item(order_id=order_id, item_id=item_id, actor=actor, operator_only=True)
    if _input_replay(
        item, expected_event_id, OrderItem.Status.QUEUED, OrderItem.Status.AWAITING_INPUT
    ):
        return item
    if item.status != OrderItem.Status.QUEUED or item.paid_at is None:
        raise ValidationError(gettext_lazy("این محصول در صف انجام نیست."))
    item.input_requested_at = timezone.now()
    item.sla_paused_at = item.input_requested_at if item.due_at is not None else None
    item.status = OrderItem.Status.AWAITING_INPUT
    item.save(update_fields=["status", "input_requested_at", "sla_paused_at"])
    _input_event(item, account, OrderItem.Status.QUEUED)
    return item


@transaction.atomic
def _receive_input(
    *, order_id: int, item_id: int, actor: User, expected_event_id: int, first: bool
) -> OrderItem:
    item, account = _input_item(
        order_id=order_id, item_id=item_id, actor=actor, operator_only=False
    )
    if _input_replay(
        item, expected_event_id, OrderItem.Status.AWAITING_INPUT, OrderItem.Status.QUEUED
    ):
        return item
    if (
        item.status != OrderItem.Status.AWAITING_INPUT
        or item.paid_at is None
        or (item.due_at is None) != first
    ):
        raise ValidationError(gettext_lazy("این محصول منتظر دریافت اطلاعات نیست."))
    at = timezone.now()
    if first:
        item.input_received_at = at
        item.due_at = delivery_deadline(
            paid_at=item.paid_at,
            delivery_hours=item.product_snapshot["delivery_hours"],
            requires_input=item.clock_requires_input,
            input_received_at=item.input_received_at,
        )
    else:
        if item.sla_paused_at is None or item.due_at is None:
            raise ValidationError(gettext_lazy("مهلت تحویل این محصول متوقف نشده است."))
        item.due_at += at - item.sla_paused_at
    item.sla_paused_at = None
    item.status = OrderItem.Status.QUEUED
    item.save(update_fields=["status", "due_at", "input_received_at", "sla_paused_at"])
    _input_event(item, account, OrderItem.Status.AWAITING_INPUT)
    return item


def receive_input(*, order_id: int, item_id: int, actor: User, expected_event_id: int) -> OrderItem:
    return _receive_input(
        order_id=order_id,
        item_id=item_id,
        actor=actor,
        expected_event_id=expected_event_id,
        first=True,
    )


def resume_input(*, order_id: int, item_id: int, actor: User, expected_event_id: int) -> OrderItem:
    return _receive_input(
        order_id=order_id,
        item_id=item_id,
        actor=actor,
        expected_event_id=expected_event_id,
        first=False,
    )


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
        raise ValidationError("فقط سفارشی که منتظر پرداخت است از اینجا لغو می‌شود.")
    # Legacy orders may already contain individually cancelled units.
    pending = [item for item in items if item.status == OrderItem.Status.PENDING_PAYMENT]
    quantities = Counter(item.plan_id for item in pending if item.stock_debited)
    for plan_id in sorted(quantities):
        plan = plans[plan_id]
        if plan.stock_quantity is not None:
            quantity = plan.stock_quantity + quantities[plan_id]
            if quantity > 2147483647:
                raise ValidationError(
                    "این سفارش فعلاً لغو نمی‌شود. به پشتیبانی پیام بدهید تا موجودی را بررسی کند."
                )
            plan.stock_quantity = quantity
            Plan.objects.filter(pk=plan_id).update(stock_quantity=quantity)
    redemption = DiscountRedemption.objects.filter(order=order, released_at__isnull=True).first()
    if redemption is not None:
        if code is None or code.used_count < 1:
            raise ValidationError(
                "این سفارش فعلاً لغو نمی‌شود. به پشتیبانی پیام بدهید تا کد تخفیف را بررسی کند."
            )
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

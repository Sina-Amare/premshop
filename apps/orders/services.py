"""Atomic creation and cancellation of unpaid orders."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.catalog.pricing import effective_price
from apps.orders.models import Order, OrderItem, OrderItemEvent


@dataclass(frozen=True)
class OrderLine:
    plan: Plan
    quantity: int
    customer_input: str | None = None


@transaction.atomic
def place_order(
    *, user: User, lines: Sequence[OrderLine], phone: str = "", channel: str = Order.Channel.WEB
) -> Order:
    if not user.pk or not User.objects.filter(pk=user.pk, is_active=True).exists():
        raise PermissionDenied
    if not lines or channel not in (Order.Channel.WEB, Order.Channel.BOT):
        raise ValidationError("اطلاعات سفارش درست نیست.")
    ids = [line.plan.pk for line in lines]
    if len(set(ids)) != len(ids):
        raise ValidationError("هر پلن را فقط یک بار به سفارش اضافه کنید.")
    plans = {
        plan.pk: plan
        for plan in Plan.objects.select_related("product")
        .prefetch_related("product__specs")
        .filter(pk__in=ids, is_available=True, product__in=Product.objects.public())
    }
    prepared = []
    subtotal = Decimal(0)
    for line in lines:
        plan = plans.get(line.plan.pk)
        if plan is None:
            raise ValidationError("این پلن الان قابل خرید نیست.")
        if type(line.quantity) is not int or not 1 <= line.quantity <= plan.max_quantity:
            raise ValidationError("تعداد انتخاب‌شده برای این پلن مجاز نیست.")
        value = line.customer_input or ""
        if not isinstance(value, str) or len(value) > 2000:
            raise ValidationError("اطلاعات فعال‌سازی درست نیست.")
        if plan.requires_customer_input and (not value.strip() or line.quantity != 1):
            raise ValidationError("اطلاعات فعال‌سازی را برای یک حساب وارد کنید.")
        product = plan.product
        snapshot = {
            "name": product.name,
            "plan_title": plan.title,
            "warranty": product.warranty,
            "delivery_type": product.delivery_type,
            "delivery_hours": product.delivery_hours,
            "delivery_note": product.delivery_note,
            "duration_days": plan.duration_days,
            "usage_notes": product.usage_notes,
            "refund_note": product.refund_note,
            "requires_customer_input": plan.requires_customer_input,
            "customer_input_label": plan.customer_input_label,
            "specs": [{"title": spec.title, "value": spec.value} for spec in product.specs.all()],
        }
        price = effective_price(plan)
        prepared.append((line, plan, value, snapshot, price))
        subtotal += price * line.quantity
    order = Order.objects.create(
        user=user, subtotal=subtotal, total_amount=subtotal, channel=channel
    )
    for line, plan, value, snapshot, price in prepared:
        for _ in range(line.quantity):
            item = OrderItem.objects.create(
                order=order,
                plan=plan,
                price_snapshot=price,
                cost_snapshot=plan.cost_price,
                product_snapshot=snapshot,
                customer_input=value,
            )
            OrderItemEvent.objects.create(
                order_item=item,
                from_status=None,
                to_status=item.status,
                actor=OrderItemEvent.Actor.CUSTOMER,
                actor_user=user,
            )
    if phone:
        if not isinstance(phone, str) or len(phone) > 20:
            raise ValidationError("شمارهٔ موبایل درست نیست.")
        User.objects.filter(pk=user.pk, phone="").update(phone=phone)
    return order


@transaction.atomic
def cancel_unpaid_item(*, item_id: int, actor: User, note: str = "") -> OrderItem:
    try:
        account = User.objects.get(pk=actor.pk, is_active=True)
    except User.DoesNotExist:
        raise PermissionDenied from None
    item = OrderItem.objects.select_for_update().defer("customer_input").get(pk=item_id)
    operator = account.is_staff
    if not operator and item.order.user_id != account.pk:
        raise PermissionDenied
    reason = (
        OrderItem.CancelReason.OPERATOR
        if operator
        else OrderItem.CancelReason.CUSTOMER_BEFORE_PAYMENT
    )
    if item.status == OrderItem.Status.CANCELLED and item.cancel_reason == reason:
        return item
    if item.status != OrderItem.Status.PENDING_PAYMENT:
        raise ValidationError("فقط آیتم منتظر پرداخت از این مسیر لغو می‌شود.")
    if operator and not note.strip():
        raise ValidationError("دلیل لغو را بنویسید.")
    item.status = OrderItem.Status.CANCELLED
    item.cancel_reason = reason
    item.save(update_fields=["status", "cancel_reason"])
    OrderItemEvent.objects.create(
        order_item=item,
        from_status=OrderItem.Status.PENDING_PAYMENT,
        to_status=item.status,
        actor=OrderItemEvent.Actor.OPERATOR if operator else OrderItemEvent.Actor.CUSTOMER,
        actor_user=account,
        note=note if operator else "",
    )
    return item

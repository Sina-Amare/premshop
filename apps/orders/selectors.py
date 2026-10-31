"""Customer purchase facts and a smaller public tracking projection."""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from django.db.models import Exists, OuterRef, Prefetch, QuerySet
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.orders.models import Order, OrderItem, OrderItemEvent

_LABELS = dict(OrderItem.Status.choices)
_STAGES = [
    OrderItem.Status.PENDING_PAYMENT,
    OrderItem.Status.QUEUED,
    OrderItem.Status.AWAITING_INPUT,
    OrderItem.Status.REPLACEMENT_REQUESTED,
]


def current_unpaid_order(user: User) -> Order | None:
    pending = OrderItem.objects.filter(
        order_id=OuterRef("pk"), status=OrderItem.Status.PENDING_PAYMENT
    )
    return (
        Order.objects.filter(user_id=user.pk, unpaid_expires_at__gt=timezone.now())
        .filter(Exists(pending))
        .order_by("pk")
        .first()
    )


def customer_orders(user: User) -> QuerySet[Order]:
    return Order.objects.filter(user_id=user.pk).prefetch_related(
        Prefetch(
            "items",
            queryset=OrderItem.objects.select_related("plan__product")
            .only(
                "pk",
                "order_id",
                "price_snapshot",
                "product_snapshot",
                "status",
                "created_at",
                "plan",
                "plan__product",
                "plan__product__name",
                "plan__product__image",
                "plan__product__brand_colour",
            )
            .order_by("pk"),
        )
    )


def current_order(user: User) -> Order | None:
    active = OrderItem.objects.filter(order_id=OuterRef("pk"), status__in=_STAGES)
    return customer_orders(user).filter(Exists(active)).first()


def status_label(values: Iterable[str]) -> str:
    statuses = set(values)
    if len(statuses) == 1:
        return str(_LABELS[next(iter(statuses))])
    for stage in _STAGES:
        if stage in statuses:
            return str(_LABELS[stage])
    return "پایان‌یافته" if statuses else "در حال بررسی"


def grouped_items(items: Iterable[OrderItem]) -> list[dict[str, Any]]:
    categories: dict[tuple[Any, str], dict[str, Any]] = {}
    for item in items:
        facts = item.product_snapshot
        category = (facts.get("category_id"), facts.get("category_name") or "محصولات سفارش")
        group = categories.setdefault(category, {"name": category[1], "lines": []})
        line = next(
            (
                existing
                for existing in group["lines"]
                if existing["facts"] == facts
                and existing["price"] == item.price_snapshot
                and existing["status"] == item.status
            ),
            None,
        )
        if line is None:
            line = {
                "facts": facts,
                "product": item.plan.product if item.plan_id else None,
                "quantity": 0,
                "price": item.price_snapshot,
                "amount": Decimal(0),
                "status": item.status,
                "status_label": str(_LABELS[item.status]),
                "warranty": str(dict(Product.Warranty.choices).get(facts.get("warranty"), "")),
                "delivery": str(
                    dict(Product.DeliveryType.choices).get(facts.get("delivery_type"), "")
                ),
            }
            group["lines"].append(line)
        line["quantity"] += 1
        line["amount"] += item.price_snapshot
    return list(categories.values())


def customer_summary(order: Order) -> dict[str, Any]:
    items = list(order.items.all())
    statuses = {item.status for item in items}
    return {
        "order": order,
        "status": status_label(statuses),
        "mixed_status": len(statuses) > 1,
        "groups": grouped_items(items),
        "can_cancel": bool(items)
        and OrderItem.Status.PENDING_PAYMENT in statuses
        and statuses <= {OrderItem.Status.PENDING_PAYMENT, OrderItem.Status.CANCELLED},
        "overdue": OrderItem.Status.PENDING_PAYMENT in statuses
        and order.unpaid_expires_at <= timezone.now(),
    }


def customer_timeline(order: Order) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for name, status, at in (
        OrderItemEvent.objects.filter(order_item__order=order)
        .order_by("created_at", "pk")
        .values_list("order_item__product_snapshot__name", "to_status", "created_at")
    ):
        key = (name, status, at.replace(microsecond=0))
        if key not in seen:
            seen.add(key)
            result.append({"name": name, "status": str(_LABELS[status]), "at": at})
    return result


def order_for_tracking(token: str) -> list[dict[str, Any]] | None:
    order_id = Order.objects.filter(tracking_token=token).values_list("pk", flat=True).first()
    if order_id is None:
        return None
    rows = list(
        OrderItem.objects.filter(order_id=order_id)
        .order_by("pk")
        .values("pk", "product_snapshot__name", "status", "created_at")
    )
    groups: dict[str, dict[str, Any]] = {}
    names = {}
    for row in rows:
        name = row["product_snapshot__name"]
        names[row["pk"]] = name
        group = groups.setdefault(
            name,
            {"name": name, "statuses": set(), "created_at": row["created_at"], "timeline": []},
        )
        group["statuses"].add(row["status"])
    seen = set()
    for item_id, status, at in (
        OrderItemEvent.objects.filter(order_item__order_id=order_id)
        .order_by("created_at", "pk")
        .values_list("order_item_id", "to_status", "created_at")
    ):
        name = names[item_id]
        key = (name, status, at.replace(second=0, microsecond=0))
        if key not in seen:
            seen.add(key)
            groups[name]["timeline"].append({"status": str(_LABELS[status]), "at": at})
    return [
        {
            "name": group["name"],
            "status": status_label(group["statuses"]),
            "created_at": group["created_at"],
            "timeline": group["timeline"],
        }
        for group in groups.values()
    ]

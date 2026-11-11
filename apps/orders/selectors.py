"""Customer purchase facts and a smaller public tracking projection."""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from math import ceil
from typing import Any

from django.db.models import Exists, F, OuterRef, Prefetch, Q, QuerySet
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.orders import status as order_status
from apps.orders.models import Delivery, Order, OrderItem, OrderItemEvent
from apps.payments.models import Payment, Refund

S = OrderItem.Status
_LABELS = dict(S.choices)
# States that still owe the customer something; the account page leads with the most urgent.
_OPEN = {"awaiting_input", "unpaid", "refund_owed", "late", "replacement", "queued"}
# The history's words, by the move an event records: the item's status before and after.
_MOVES: dict[tuple[str | None, str], str] = {
    (S.PENDING_PAYMENT, S.QUEUED): "پرداخت تأیید شد",
    (S.PENDING_PAYMENT, S.AWAITING_INPUT): "پرداخت تأیید شد",
    (S.AWAITING_INPUT, S.QUEUED): "اطلاعات فعال‌سازی رسید",
    (S.QUEUED, S.QUEUED): "زمان تحویل تغییر کرد",
}
_ARRIVALS: dict[str, str] = {
    S.PENDING_PAYMENT: "سفارش ثبت شد",
    S.QUEUED: "پرداخت تأیید شد",
    S.AWAITING_INPUT: "اطلاعات فعال‌سازی درخواست شد",
    S.DELIVERED: "تحویل شد",
    S.REPLACEMENT_REQUESTED: "درخواست تعویض ثبت شد",
    S.CANCELLED: "لغو شد",
    S.REFUNDED: "وجه برگشت داده شد",
}


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
            queryset=OrderItem.objects.select_related("plan__product", "refund")
            .only(
                "pk",
                "order_id",
                "price_snapshot",
                "product_snapshot",
                "status",
                "created_at",
                "paid_at",
                "due_at",
                "sla_paused_at",
                "input_requested_at",
                "cancel_reason",
                "delivered_at",
                "expires_at",
                "plan",
                "plan__product",
                "plan__product__name",
                "plan__product__image",
                "plan__product__brand_colour",
                "refund__order_item",
                "refund__amount",
                "refund__status",
                "refund__executed_at",
            )
            .order_by("pk"),
        ),
        Prefetch(
            "payments",
            queryset=Payment.objects.filter(status=Payment.Status.VERIFIED).only(
                "pk", "order_id", "amount", "verified_at"
            ),
            to_attr="verified_payments",
        ),
    )


def current_order(user: User) -> Order | None:
    """The order the customer most needs to hear about: the highest-ranked open state,
    the newest first among equals."""
    best: tuple[int, Order] | None = None
    for order in customer_orders(user).order_by("-pk")[:20]:
        state = customer_summary(order)["lead"]["state"]
        rank = order_status.RANK.index(state)
        if state in _OPEN and (best is None or rank < best[0]):
            best = (rank, order)
    return best[1] if best else None


def grouped_items(
    items: Iterable[OrderItem], states: dict[int, str] | None = None
) -> list[dict[str, Any]]:
    """Lines by category. With `states`, each line also carries the customer's facts:
    its pill, refund, delivery time, delivery and subscription dates and warranty."""
    categories: dict[tuple[Any, str], dict[str, Any]] = {}
    for item in items:
        facts = item.product_snapshot
        state = states.get(item.pk) if states is not None else None
        category = (facts.get("category_id"), facts.get("category_name") or "محصولات سفارش")
        group = categories.setdefault(category, {"name": category[1], "lines": []})
        line = next(
            (
                existing
                for existing in group["lines"]
                if existing["facts"] == facts
                and existing["price"] == item.price_snapshot
                and existing["status"] == item.status
                and existing["state"] == state
            ),
            None,
        )
        if line is None:
            pill = order_status.pill(state) if state else None
            line = {
                "facts": facts,
                "product": item.plan.product if item.plan_id else None,
                "quantity": 0,
                "price": item.price_snapshot,
                "amount": Decimal(0),
                "status": item.status,
                "state": state,
                "pill": pill,
                "status_label": pill["label"] if pill else str(_LABELS[item.status]),
                "warranty": str(dict(Product.Warranty.choices).get(facts.get("warranty"), "")),
                "delivery": str(
                    dict(Product.DeliveryType.choices).get(facts.get("delivery_type"), "")
                ),
                "refund": Decimal(0),
                "refunded_at": None,
                "due_at": None,
                "delivered_at": None,
                "expires_at": None,
                "warranty_end": None,
            }
            group["lines"].append(line)
        line["quantity"] += 1
        line["amount"] += item.price_snapshot
        if states is not None:
            _add_customer_facts(line, item)
    return list(categories.values())


def _add_customer_facts(line: dict[str, Any], item: OrderItem) -> None:
    refund = getattr(item, "refund", None)
    if refund is not None:
        line["refund"] += refund.amount
        if refund.executed_at and (
            line["refunded_at"] is None or refund.executed_at > line["refunded_at"]
        ):
            line["refunded_at"] = refund.executed_at
    if item.due_at and (line["due_at"] is None or item.due_at < line["due_at"]):
        line["due_at"] = item.due_at
    line["delivered_at"] = line["delivered_at"] or item.delivered_at
    line["expires_at"] = line["expires_at"] or item.expires_at
    line["warranty_end"] = line["warranty_end"] or order_status.warranty_last_day(item)


def customer_summary(order: Order) -> dict[str, Any]:
    items = list(order.items.all())
    now = timezone.now()
    refunds = {item.pk: getattr(item, "refund", None) for item in items}
    states = {
        item.pk: order_status.item_state(item, refunds[item.pk], order.unpaid_expires_at, now)
        for item in items
    }
    lead = order_status.lead_state(list(states.values()))
    statuses = {item.status for item in items}
    groups = grouped_items(items, states)
    return {
        "order": order,
        "status": order_status.pill(lead)["label"],
        "lead": order_status.pill(lead),
        "mixed_status": len(set(states.values())) > 1,
        "groups": groups,
        # One refund line's amount is already the card's heading and a totals row.
        "refund_lines": sum(
            line["state"] in ("refund_owed", "refunded")
            for group in groups
            for line in group["lines"]
        ),
        "can_cancel": bool(items)
        and S.PENDING_PAYMENT in statuses
        and statuses <= {S.PENDING_PAYMENT, S.CANCELLED},
        # What the customer should know first: the order page and the bot's panel lead with it.
        "card": _card(order, lead, [item for item in items if states[item.pk] == lead], refunds),
        "money": _money(order, refunds),
    }


def _card(
    order: Order, lead: str, items: list[OrderItem], refunds: dict[int, Refund | None]
) -> dict[str, Any]:
    card: dict[str, Any] = {
        **order_status.pill(lead),
        # Writing to support is the action only here; elsewhere support waits in its disclosure.
        "contact": lead in {"unpaid", "awaiting_input", "refund_owed"},
        "products": list(dict.fromkeys(item.product_snapshot.get("name", "") for item in items)),
        "deadline": order.unpaid_expires_at,
        "total": order.total_amount,
        "cancelled_at": order.cancelled_at,
        "reason": "",
        "paid": False,
    }
    if not items:
        return card
    first = items[0]
    rows = [refund for item in items if (refund := refunds[item.pk]) is not None]
    warranty_end = order_status.warranty_last_day(first)
    card.update(
        reason=first.cancel_reason or "",
        paid=first.paid_at is not None,
        due_at=min((item.due_at for item in items if item.due_at), default=None),
        hours=_hours_after_details(first),
        delivered_at=first.delivered_at,
        expires_at=first.expires_at,
        warranty_end=warranty_end,
        in_warranty=warranty_end is not None and timezone.localdate() <= warranty_end,
        refund=sum((row.amount for row in rows), Decimal(0)),
        refunded_at=max((row.executed_at for row in rows if row.executed_at), default=None),
    )
    return card


def _hours_after_details(item: OrderItem) -> int | None:
    """What the delivery promise will be once the details arrive: a paused clock keeps what
    was left of it, and a first request starts the product's full time."""
    if item.sla_paused_at and item.due_at:
        return max(1, ceil((item.due_at - item.sla_paused_at).total_seconds() / 3600))
    hours = item.product_snapshot.get("delivery_hours")
    return int(hours) if hours else None


def _money(order: Order, refunds: dict[int, Refund | None]) -> dict[str, Any]:
    """Money that moved, read from the ledger rows and never worked out here."""
    payments = getattr(order, "verified_payments", None)
    if payments is None:
        payments = list(order.payments.filter(status=Payment.Status.VERIFIED))
    paid = payments[0] if payments else None
    rows = [refund for refund in refunds.values() if refund is not None]
    return {
        "paid": paid.amount if paid else None,
        "paid_at": paid.verified_at if paid else None,
        "returned": sum(
            (row.amount for row in rows if row.status == Refund.Status.EXECUTED), Decimal(0)
        ),
        "owed": sum((row.amount for row in rows if row.status == Refund.Status.OWED), Decimal(0)),
    }


def _history(**where: Any) -> Iterable[tuple[int, str, str | None, str, datetime, bool]]:
    return (
        OrderItemEvent.objects.filter(**where)
        # A deletion or a repeated request is not news; a moved delivery time is.
        .exclude(Q(from_status=F("to_status")) & ~Q(to_status=S.QUEUED))
        .annotate(delivered=Exists(Delivery.objects.filter(event_id=OuterRef("pk"))))
        .order_by("created_at", "pk")
        .values_list(
            "order_item_id",
            "order_item__product_snapshot__name",
            "from_status",
            "to_status",
            "created_at",
            "delivered",
        )
    )


def _words(before: str | None, after: str, delivered: bool) -> str:
    if before == S.REPLACEMENT_REQUESTED and after == S.DELIVERED:
        return "تعویض انجام شد" if delivered else "درخواست تعویض رد شد"
    return _MOVES.get((before, after)) or _ARRIVALS[after]


def customer_timeline(order: Order) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for _, name, before, after, at, delivered in _history(order_item__order=order):
        words = _words(before, after, delivered)
        key = (name, words, at.replace(microsecond=0))
        if key not in seen:
            seen.add(key)
            result.append({"name": name, "status": words, "at": at})
    return result


def order_for_tracking(token: str) -> list[dict[str, Any]] | None:
    """Item statuses and their history, with no money, refund or clock behind them."""
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
            name, {"name": name, "states": [], "created_at": row["created_at"], "timeline": []}
        )
        group["states"].append(order_status.tracking_state(row["status"]))
    seen = set()
    for item_id, _, before, after, at, delivered in _history(order_item__order_id=order_id):
        name = names[item_id]
        words = _words(before, after, delivered)
        key = (name, words, at.replace(second=0, microsecond=0))
        if key not in seen:
            seen.add(key)
            groups[name]["timeline"].append({"status": words, "at": at})
    tracked = []
    for group in groups.values():
        pill = order_status.pill(order_status.lead_state(group["states"]))
        tracked.append(
            {
                "name": group["name"],
                "pill": pill,
                "status": pill["label"],
                "created_at": group["created_at"],
                "timeline": group["timeline"],
            }
        )
    return tracked

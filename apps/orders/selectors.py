"""Customer purchase facts and a smaller public tracking projection."""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from math import ceil
from typing import Any

from django.db.models import Exists, F, OuterRef, Prefetch, Q, QuerySet, Subquery
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
# An item's latest move: when a replacement was asked for, and whether the answer was no.
_LAST_MOVE = (
    OrderItemEvent.objects.filter(order_item_id=OuterRef("pk"))
    .exclude(from_status=F("to_status"))
    .order_by("-created_at", "-pk")
)
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
            .annotate(
                moved_from=Subquery(_LAST_MOVE.values("from_status")[:1]),
                moved_at=Subquery(_LAST_MOVE.values("created_at")[:1]),
                moved_by=Subquery(_LAST_MOVE.values("pk")[:1]),
            )
            .annotate(
                moved_with_delivery=Exists(Delivery.objects.filter(event_id=OuterRef("moved_by")))
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


def order_lead(order: Order, now: datetime) -> str:
    """The state an order leads with, ranked as on its page, without building the page."""
    return order_status.lead_state(
        [
            order_status.item_state(
                item, getattr(item, "refund", None), order.unpaid_expires_at, now
            )
            for item in order.items.all()
        ]
    )


def current_order(user: User) -> Order | None:
    """The order the customer most needs to hear about: the highest-ranked open state,
    the newest first among equals."""
    still_open = OrderItem.objects.filter(order_id=OuterRef("pk")).filter(
        Q(status__in=[S.PENDING_PAYMENT, S.AWAITING_INPUT, S.QUEUED, S.REPLACEMENT_REQUESTED])
        | Q(refund__status=Refund.Status.OWED)
    )
    best: tuple[int, Order] | None = None
    for order in customer_orders(user).filter(Exists(still_open)).order_by("-pk"):
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
        key = _line_key(item) if states is not None else None
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
                and existing["key"] == key
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
                "key": key,
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


def _day(moment: datetime | None) -> date | None:
    return timezone.localdate(moment) if moment else None


def _line_key(item: OrderItem) -> tuple[Any, ...]:
    """Units merge into one line only when every fact the line prints is the same."""
    refund = getattr(item, "refund", None)
    sent = refund.executed_at if refund is not None else None
    return (_day(item.delivered_at), item.expires_at, item.cancel_reason, _day(sent))


def _add_customer_facts(line: dict[str, Any], item: OrderItem) -> None:
    refund = getattr(item, "refund", None)
    if refund is not None:
        line["refund"] += refund.amount
        if refund.executed_at and (
            line["refunded_at"] is None or refund.executed_at > line["refunded_at"]
        ):
            line["refunded_at"] = refund.executed_at
    if item.due_at and (line["due_at"] is None or item.due_at > line["due_at"]):
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
    card = _card(order, lead, [item for item in items if states[item.pk] == lead], refunds)
    return {
        "order": order,
        "status": order_status.pill(lead)["label"],
        "lead": order_status.pill(lead),
        "mixed_status": len(set(states.values())) > 1,
        # Each line prints its own dates when the card's one date cannot speak for all.
        "line_facts": len(set(states.values())) > 1 or not card["uniform"],
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
        "card": card,
        "money": _money(order, refunds),
    }


def _card(
    order: Order, lead: str, items: list[OrderItem], refunds: dict[int, Refund | None]
) -> dict[str, Any]:
    card: dict[str, Any] = {
        **order_status.pill(lead),
        "products": list(dict.fromkeys(item.product_snapshot.get("name", "") for item in items)),
        "deadline": order.unpaid_expires_at,
        "total": order.total_amount,
        "cancelled_at": order.cancelled_at,
        "reason": "",
        "reasons": [],
        "paid": False,
        "uniform": True,
        "in_warranty": False,
        "rejected_at": None,
    }
    if items:
        card.update(_card_facts(lead, items, refunds))
    # Writing to support is the action only here; elsewhere support waits in its disclosure.
    card["contact"] = (
        lead in {"unpaid", "awaiting_input", "refund_owed", "late"}
        or (lead == "delivered" and (card["in_warranty"] or card["rejected_at"] is not None))
        or (
            lead == "cancelled"
            and card["reason"] == OrderItem.CancelReason.OPERATOR
            and not card["paid"]
        )
    )
    return card


def _card_facts(
    lead: str, items: list[OrderItem], refunds: dict[int, Refund | None]
) -> dict[str, Any]:
    rows = [refund for item in items if (refund := refunds[item.pk]) is not None]
    ends = [order_status.warranty_last_day(item) for item in items]
    by_reason: dict[str, list[str]] = {}
    for item in items:
        names = by_reason.setdefault(item.cancel_reason or "", [])
        if (name := item.product_snapshot.get("name", "")) not in names:
            names.append(name)
    dates = {
        (_day(item.delivered_at), item.expires_at, end)
        for item, end in zip(items, ends, strict=True)
    }
    sent = [row.executed_at for row in rows if row.executed_at]
    sent_days = {_day(moment) for moment in sent}
    dues = [item.due_at for item in items if item.due_at]
    hours = [found for item in items if (found := _hours_after_details(item)) is not None]
    today = timezone.localdate()
    return {
        "reason": next(iter(by_reason)) if len(by_reason) == 1 else "",
        "reasons": [
            {"reason": key, "products": "، ".join(names)} for key, names in by_reason.items()
        ],
        "paid": any(item.paid_at for item in items),
        # The promise covers every item; a delay counts from the first time missed.
        "due_at": (min if lead == "late" else max)(dues, default=None),
        "hours": max(hours, default=None),
        "delivered_at": items[0].delivered_at,
        "expires_at": items[0].expires_at,
        "warranty_end": ends[0],
        "in_warranty": any(end is not None and today <= end for end in ends),
        "refund": sum((row.amount for row in rows), Decimal(0)),
        "refunded_at": max(sent) if len(sent_days) == 1 else None,
        "requested_at": max(
            (
                moved
                for item in items
                if item.status == S.REPLACEMENT_REQUESTED
                and (moved := getattr(item, "moved_at", None))
            ),
            default=None,
        ),
        "rejected_at": max(
            (
                moved
                for item in items
                if _rejected(item) and (moved := getattr(item, "moved_at", None))
            ),
            default=None,
        ),
        "uniform": len(dates) == 1 and len(sent_days) <= 1,
    }


def _rejected(item: OrderItem) -> bool:
    """A replacement request answered with no: back to delivered with nothing delivered."""
    return (
        item.status == S.DELIVERED
        and getattr(item, "moved_from", None) == S.REPLACEMENT_REQUESTED
        and not getattr(item, "moved_with_delivery", True)
    )


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
        .values("pk", "product_snapshot__name", "status", "cancel_reason", "created_at")
    )
    groups: dict[str, dict[str, Any]] = {}
    names = {}
    for row in rows:
        name = row["product_snapshot__name"]
        names[row["pk"]] = name
        group = groups.setdefault(
            name, {"name": name, "states": [], "created_at": row["created_at"], "timeline": []}
        )
        group["states"].append(
            order_status.tracking_state(row["status"], row["cancel_reason"] or "")
        )
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

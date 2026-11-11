"""How each order state reads and looks to the customer: its words, its tone and its icon,
and the one ranking that decides which state an order leads with. The order pages, the
account card, tracking, the invoice and the bot read it here, so they cannot disagree."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from apps.catalog.models import Product
from apps.orders.models import OrderItem

if TYPE_CHECKING:
    from apps.payments.models import Refund

S = OrderItem.Status
REASON = OrderItem.CancelReason

# The five tones of docs/design-language.md, as the site's badge classes and icons.
BADGE = {"attention": "time", "progress": "progress", "done": "ok", "closed": "neutral",
         "problem": "danger"}  # fmt: skip
ICON = {"attention": "clock", "progress": "progress", "done": "check", "closed": "close",
        "problem": "alert"}  # fmt: skip

# The customer's words and the tone, in ranking order: the customer's own moves first,
# then what went wrong, work in hand, what is finished, and what is closed.
STATES: dict[str, tuple[str, str]] = {
    "awaiting_input": ("در انتظار اطلاعات شما", "attention"),
    "unpaid": ("در انتظار پرداخت", "attention"),
    "refund_owed": ("در انتظار بازگشت وجه", "attention"),
    "late": ("تأخیر در تحویل", "problem"),
    "replacement": ("بررسی درخواست تعویض", "progress"),
    "queued": ("در حال آماده‌سازی", "progress"),
    "delivered": ("تحویل‌شده", "done"),
    "refunded": ("بازگشت وجه تکمیل‌شده", "closed"),
    "expired": ("پایان مهلت پرداخت", "closed"),
    # Red by the owner's choice (2026-11-11), yet last in the ranking: any other item says more.
    "cancelled": ("لغوشده", "problem"),
}
RANK = list(STATES)
# A cancellation asks nothing of anyone, so it keeps the cross rather than a delay's alert.
STATE_ICONS = {"cancelled": "close"}


def item_state(
    item: OrderItem, refund: Refund | None, unpaid_until: datetime, now: datetime
) -> str:
    """One item's state as the customer meets it. Whether money moved decides a cancelled
    item's words, read from its refund, never guessed from the reason."""
    if item.status == S.PENDING_PAYMENT:
        return "unpaid" if unpaid_until > now else "expired"
    if item.status == S.AWAITING_INPUT:
        return "awaiting_input"
    if item.status == S.QUEUED:
        return "late" if item.due_at is not None and item.due_at <= now else "queued"
    if item.status == S.REPLACEMENT_REQUESTED:
        return "replacement"
    if item.status == S.DELIVERED:
        return "delivered"
    if item.status == S.REFUNDED:
        return "refunded"
    if item.status != S.CANCELLED:
        raise ValueError(f"No customer state for item status {item.status!r}")
    if refund is not None:
        return "refund_owed" if refund.status == refund.Status.OWED else "refunded"
    return "expired" if item.cancel_reason == REASON.EXPIRED_UNPAID else "cancelled"


def lead_state(states: list[str]) -> str:
    return min(states, key=RANK.index) if states else "cancelled"


# The public tracking page reads statuses only, with no refund or clock behind them.
_TRACKING: dict[str, str] = {
    S.PENDING_PAYMENT: "unpaid",
    S.AWAITING_INPUT: "awaiting_input",
    S.QUEUED: "queued",
    S.REPLACEMENT_REQUESTED: "replacement",
    S.DELIVERED: "delivered",
    S.REFUNDED: "refunded",
}


def tracking_state(status: str, cancel_reason: str = "") -> str:
    if status == S.CANCELLED:
        return "expired" if cancel_reason == REASON.EXPIRED_UNPAID else "cancelled"
    if status not in _TRACKING:
        raise ValueError(f"No customer state for item status {status!r}")
    return _TRACKING[status]


def pill(state: str) -> dict[str, Any]:
    label, tone = STATES[state]
    icon = STATE_ICONS.get(state, ICON[tone])
    return {"state": state, "label": label, "tone": tone, "badge": BADGE[tone], "icon": icon}


def warranty_last_day(item: OrderItem) -> date | None:
    """The last day a replacement claim is accepted: `services.refund_amount` holds the same
    rule, and a test keeps the two equal."""
    if item.delivered_at is None:
        return None
    delivered = timezone.localdate(item.delivered_at)
    warranty = item.product_snapshot.get("warranty")
    if warranty == Product.Warranty.DAYS_7:
        end = delivered + timedelta(days=7)
        return min(end, item.expires_at) if item.expires_at else end
    if warranty == Product.Warranty.FULL_PERIOD:
        return item.expires_at
    return None

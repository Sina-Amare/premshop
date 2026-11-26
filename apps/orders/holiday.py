"""The owner's holiday: while one is open, delivery clocks stand still (ADR-0009).

A clock that would start during a holiday starts stopped, and ending the holiday moves each
stopped deadline by the time it stood still. Every path that starts a delivery clock takes
`lock` shared, after its buyer, order and payment rows and before its items. New order
placement takes the same lock before checking whether the shop is taking orders. The switch
takes it exclusively and then locks only items. A write sees either side of the switch, and
the switch never waits for the buyer, order or payment locks already held by writers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Min
from django.utils import timezone
from django.utils.translation import gettext_lazy

from apps.accounts.models import User
from apps.core.formatting import format_jalali, to_persian_digits
from apps.orders.clock import resumed_deadline
from apps.orders.models import Holiday, OrderItem, OrderItemEvent

# A fixed key no other advisory lock in the project uses ("HOLI" in ASCII).
_LOCK_KEY = 0x484F4C49
PAUSE_NOTE = "شروع تعطیلی"
STALE = gettext_lazy("وضعیت تعطیلی تغییر کرد. صفحه را دوباره باز کنید.")
CHECKOUT_CLOSED = "تا پایان تعطیلی سفارش جدید نمی‌گیریم."
CHECKOUT_CLOSED_WITH_CART = (
    "تا پایان تعطیلی سفارش جدید نمی‌گیریم و محصولاتی که انتخاب کرده‌اید، در سبد خریدتان می‌مانند."
)


def lock(*, exclusive: bool = False) -> None:
    sql = (
        "SELECT pg_advisory_xact_lock(%s)"
        if exclusive
        else "SELECT pg_advisory_xact_lock_shared(%s)"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [_LOCK_KEY])


def current() -> Holiday | None:
    return Holiday.objects.filter(ended_at=None).first()


def clock_allows_delivery(item: OrderItem) -> bool:
    return item.sla_paused_at is None or current() is not None


def hold(item: OrderItem, started: datetime, opened: Holiday | None) -> None:
    """Stops a clock starting at `started` while `opened` is open. The caller holds `lock`."""
    if opened is not None and item.due_at is not None and item.sla_paused_at is None:
        item.sla_paused_at = max(started, opened.started_at)


def summary(now: datetime) -> dict[str, Any]:
    """What the holiday page states: what starting would stop, or what ending would move.
    Ending's figures are an estimate for `now`; the end recomputes them at its own moment."""
    opened = current()
    if opened is None:
        running = OrderItem.objects.filter(
            status=OrderItem.Status.QUEUED, due_at__isnull=False, sla_paused_at__isnull=True
        )
        return {
            "opened": None,
            "item_count": running.count(),
            "order_count": running.values("order_id").distinct().count(),
            "earliest": running.aggregate(earliest=Min("due_at"))["earliest"],
            "waiting": OrderItem.objects.filter(status=OrderItem.Status.AWAITING_INPUT).count(),
        }
    paused = list(
        OrderItem.objects.filter(
            status=OrderItem.Status.QUEUED, sla_paused_at__isnull=False
        ).values_list("order_id", "due_at", "sla_paused_at")
    )
    return {
        "opened": opened,
        "elapsed": now - opened.started_at,
        "item_count": len(paused),
        "order_count": len({order_id for order_id, *_ in paused}),
        "earliest": min(
            (
                resumed_deadline(cast(datetime, due), cast(datetime, stopped), now)
                for _, due, stopped in paused
            ),
            default=None,
        ),
    }


def operator(actor: User) -> User:
    """The owner, or staff the owner has given this one permission."""
    account = User.objects.filter(pk=actor.pk, is_active=True, is_staff=True).first()
    if account is None or not account.has_perm("orders.change_holiday"):
        raise PermissionDenied
    return account


def _when(moment: datetime) -> str:
    local = timezone.localtime(moment)
    return f"{format_jalali(local)}، {to_persian_digits(local.strftime('%H:%M'))}"


@transaction.atomic
def start(*, actor: User, expected_latest: int | None) -> Holiday:
    """Opens a holiday; `expected_latest` is the newest holiday the page showed, if any."""
    account = operator(actor)
    lock(exclusive=True)
    latest = Holiday.objects.order_by("-pk").first()
    if latest is not None and latest.ended_at is None:
        raise ValidationError(gettext_lazy("تعطیلی قبلاً شروع شده است."))
    if (latest.pk if latest is not None else None) != expected_latest:
        raise ValidationError(STALE)
    opened = Holiday.objects.create(started_by=account, started_at=timezone.now())
    # Only item rows, never a buyer or an order: payments hold those while they wait for `lock`.
    items = list(
        OrderItem.objects.select_for_update()
        .only("pk")
        .filter(status=OrderItem.Status.QUEUED, due_at__isnull=False, sla_paused_at__isnull=True)
        .order_by("pk")
    )
    OrderItem.objects.filter(pk__in=[item.pk for item in items]).update(
        sla_paused_at=opened.started_at
    )
    _record([(item.pk, PAUSE_NOTE) for item in items], opened)
    return opened


@transaction.atomic
def end(*, actor: User, expected_open: int) -> Holiday:
    """Closes the open holiday the page showed, and restarts every clock it stopped."""
    account = operator(actor)
    lock(exclusive=True)
    opened = current()
    if opened is None:
        raise ValidationError(gettext_lazy("فروشگاه در حالت تعطیلی نیست."))
    if opened.pk != expected_open:
        raise ValidationError(STALE)
    ended = timezone.now()
    opened.ended_at, opened.ended_by = ended, account
    opened.save(update_fields=["ended_at", "ended_by"])
    items = list(
        OrderItem.objects.select_for_update()
        .only("pk", "due_at", "sla_paused_at")
        .filter(status=OrderItem.Status.QUEUED, sla_paused_at__isnull=False)
        .order_by("pk")
    )
    lines = []
    for item in items:
        # order_item_pause_requires_deadline: a stopped clock always has a deadline.
        due = resumed_deadline(
            cast(datetime, item.due_at), cast(datetime, item.sla_paused_at), ended
        )
        item.due_at, item.sla_paused_at = due, None
        lines.append((item.pk, f"پایان تعطیلی · مهلت جدید: {_when(due)}"))
    OrderItem.objects.bulk_update(items, ["due_at", "sla_paused_at"])
    _record(lines, opened)
    return opened


def _record(lines: list[tuple[int, str]], opened: Holiday) -> None:
    # The system's line, not the operator's: `ended_by` says who, and an operator's own
    # QUEUED → QUEUED line would read as a delay they had already recorded.
    OrderItemEvent.objects.bulk_create(
        OrderItemEvent(
            order_item_id=item_id,
            from_status=OrderItem.Status.QUEUED,
            to_status=OrderItem.Status.QUEUED,
            actor=OrderItemEvent.Actor.SYSTEM,
            note=note,
            holiday=opened,
        )
        for item_id, note in lines
    )

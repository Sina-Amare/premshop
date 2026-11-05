"""The purchased delivery window, measured in elapsed hours."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.core import support

TEHRAN = ZoneInfo("Asia/Tehran")


def delivery_deadline(
    *,
    paid_at: datetime | None,
    delivery_hours: int,
    requires_input: bool = False,
    input_received_at: datetime | None = None,
) -> datetime | None:
    if type(delivery_hours) is not int or not 1 <= delivery_hours <= 48:
        raise ValueError("Delivery hours must be a whole number between 1 and 48.")
    for at in (paid_at, input_received_at):
        if at is not None and timezone.is_naive(at):
            raise ValueError("Delivery clock times must include a timezone.")
    # The first wait has no running clock; it must never also become a pause.
    if paid_at is None or (requires_input and input_received_at is None):
        return None
    start = paid_at
    if requires_input and input_received_at is not None:
        start = max(start, input_received_at)
    start = start.astimezone(TEHRAN)
    due = start + timedelta(hours=delivery_hours)
    if due.hour < support.OPENS:
        due = due.replace(hour=support.OPENS, minute=0, second=0, microsecond=0)
    elif due.hour >= support.CLOSES:  # 22:00 is outside [10:00, 22:00).
        due = (due + timedelta(days=1)).replace(
            hour=support.OPENS, minute=0, second=0, microsecond=0
        )
    # The cap wins even when it falls outside the support window.
    return min(due, start + timedelta(hours=48))

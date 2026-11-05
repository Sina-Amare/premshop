from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

TEHRAN = ZoneInfo("Asia/Tehran")


def local(value):
    return datetime.fromisoformat(value).replace(tzinfo=TEHRAN)


@pytest.mark.parametrize(
    "paid,hours,expected",
    [
        ("2026-11-05T09:00", 1, "2026-11-05T10:00"),
        ("2026-11-05T10:00", 1, "2026-11-05T11:00"),
        ("2026-11-05T20:59:59", 1, "2026-11-05T21:59:59"),
        ("2026-11-05T21:00", 1, "2026-11-06T10:00"),
        ("2026-11-05T21:00:01", 1, "2026-11-06T10:00"),
        ("2026-11-05T22:00", 1, "2026-11-06T10:00"),
        ("2026-11-05T23:00", 1, "2026-11-06T10:00"),
        ("2026-11-06T00:30", 1, "2026-11-06T10:00"),
        ("2026-11-06T09:00", 1, "2026-11-06T10:00"),
        ("2026-11-06T11:00", 12, "2026-11-07T10:00"),
        ("2026-11-05T18:00", 24, "2026-11-06T18:00"),
        ("2026-11-05T11:00", 48, "2026-11-07T11:00"),
        ("2026-11-05T09:00", 48, "2026-11-07T09:00"),
        ("2026-11-05T22:00", 48, "2026-11-07T22:00"),
        ("2026-11-05T23:00", 48, "2026-11-07T23:00"),
        ("2026-11-05T23:00", 47, "2026-11-07T23:00"),
        ("2026-11-30T23:00", 1, "2026-12-01T10:00"),
        ("2026-12-31T23:00", 1, "2027-01-01T10:00"),
    ],
)
def test_delivery_clock_edge_table(paid, hours, expected):
    from apps.orders.clock import delivery_deadline

    assert delivery_deadline(paid_at=local(paid), delivery_hours=hours) == local(expected)


@pytest.mark.parametrize(
    "paid,received,required,expected",
    [
        (None, None, False, None),
        (None, "2026-11-05T12:00", True, None),
        ("2026-11-05T18:00", None, True, None),
        ("2026-11-05T18:00", "2026-11-05T12:00", True, "2026-11-05T20:00"),
        ("2026-11-05T18:00", "2026-11-06T11:00", True, "2026-11-06T13:00"),
        ("2026-11-05T18:00", "2026-11-06T11:00", False, "2026-11-05T20:00"),
    ],
)
def test_first_input_wait_has_no_deadline_and_is_never_added_twice(
    paid, received, required, expected
):
    from apps.orders.clock import delivery_deadline

    assert delivery_deadline(
        paid_at=local(paid) if paid else None,
        delivery_hours=2,
        requires_input=required,
        input_received_at=local(received) if received else None,
    ) == (local(expected) if expected else None)


def test_clock_uses_tehran_schedule_regardless_of_input_timezone_or_active_timezone(settings):
    from apps.orders.clock import delivery_deadline

    settings.TIME_ZONE = "UTC"
    assert delivery_deadline(
        paid_at=local("2026-11-05T21:00").astimezone(UTC), delivery_hours=1
    ) == local("2026-11-06T10:00")


def test_late_first_receipt_moves_the_cap_without_also_adding_the_wait():
    from apps.orders.clock import delivery_deadline

    assert delivery_deadline(
        paid_at=local("2026-11-05T12:00"),
        delivery_hours=48,
        requires_input=True,
        input_received_at=local("2026-11-10T23:00"),
    ) == local("2026-11-12T23:00")


@pytest.mark.parametrize("hours", [0, 49, -1, True, 1.5, "2", None])
def test_clock_refuses_invalid_purchased_hours(hours):
    from apps.orders.clock import delivery_deadline

    with pytest.raises(ValueError):
        delivery_deadline(paid_at=local("2026-11-05T12:00"), delivery_hours=hours)


@pytest.mark.parametrize("which", ["payment", "input"])
def test_clock_refuses_naive_times(which):
    from apps.orders.clock import delivery_deadline

    aware = local("2026-11-05T12:00")
    naive = aware.replace(tzinfo=None)
    with pytest.raises(ValueError):
        delivery_deadline(
            paid_at=naive if which == "payment" else aware,
            input_received_at=naive if which == "input" else aware,
            requires_input=True,
            delivery_hours=2,
        )

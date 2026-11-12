from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
import time_machine
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.catalog.models import Product
from apps.orders import services, status
from apps.orders.models import OrderItem
from apps.orders.tests.test_delivery import deliver
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import paid_order
from apps.orders.tests.test_replacement import claim
from apps.payments.models import Refund

S = OrderItem.Status


def test_every_state_has_words_a_known_tone_and_an_icon():
    for state, (label, tone) in status.STATES.items():
        assert label and tone in status.BADGE and tone in status.ICON, state
        assert 2 <= len(label.split()) <= 4 or label in {"تحویل‌شده", "لغوشده"}, label


def test_email_tones_are_the_panels_light_tones(settings):
    css = (settings.BASE_DIR / "static/css/admin-experience.css").read_text(encoding="utf-8")
    light = css[css.index("--panel-attention-bg") :]
    light = light[: light.index("}")].replace(" ", "")
    assert set(status.EMAIL_TONES) == set(status.BADGE)
    for tone, colours in status.EMAIL_TONES.items():
        for part, value in zip(("bg", "text", "line"), colours, strict=True):
            assert f"--panel-{tone}-{part}:{value};" in light + ";", (tone, part)


@pytest.mark.parametrize("value", S.values)
def test_every_item_status_lands_on_a_known_state(value):
    now = timezone.now()
    item = OrderItem(status=value)
    for until in (now + timedelta(hours=1), now - timedelta(hours=1)):
        assert status.item_state(item, None, until, now) in status.STATES
    assert status.tracking_state(value) in status.STATES


def test_a_cancellation_reads_red_with_its_cross_and_ranks_last():
    """The owner's choice (2026-11-11): a cancelled order is red. It asks nothing of anyone,
    so it keeps the cross rather than the alert of a delay, and any other item says more."""
    cancelled = status.pill("cancelled")
    assert (cancelled["tone"], cancelled["badge"], cancelled["icon"]) == (
        "problem",
        "danger",
        "close",
    )
    assert status.pill("late")["icon"] == "alert"
    assert status.lead_state(["late", "cancelled"]) == "late"
    assert status.lead_state(["delivered", "cancelled"]) == "delivered"
    # A lapsed payment window stays grey: amber would invite a payment it cannot take.
    assert status.pill("expired")["tone"] == "closed"


def test_an_unknown_status_fails_loudly_instead_of_reading_as_cancelled():
    now = timezone.now()
    with pytest.raises(ValueError):
        status.item_state(OrderItem(status="LOST"), None, now, now)
    with pytest.raises(ValueError):
        status.tracking_state("LOST")
    assert set(status._TRACKING) | {S.CANCELLED} == set(S.values)


@pytest.mark.parametrize(
    "reason, state",
    [
        (OrderItem.CancelReason.EXPIRED_UNPAID, "expired"),
        (OrderItem.CancelReason.CUSTOMER_BEFORE_PAYMENT, "cancelled"),
        (OrderItem.CancelReason.SUPPLY_FAILURE, "cancelled"),
    ],
)
def test_tracking_tells_a_lapsed_payment_window_from_a_cancellation(reason, state):
    assert status.tracking_state(S.CANCELLED, reason) == state


@pytest.mark.parametrize("reason", OrderItem.CancelReason.values)
def test_every_cancel_reason_lands_on_a_known_state(reason):
    now = timezone.now()
    item = OrderItem(status=S.CANCELLED, cancel_reason=reason)
    assert status.item_state(item, None, now, now) in {"expired", "cancelled"}
    owed = Refund(status=Refund.Status.OWED)
    assert status.item_state(item, owed, now, now) == "refund_owed"
    assert status.item_state(item, Refund(status=Refund.Status.EXECUTED), now, now) == "refunded"


@pytest.mark.parametrize(
    "states, lead",
    [
        (["queued", "refund_owed"], "refund_owed"),
        (["delivered", "refund_owed"], "refund_owed"),
        (["delivered", "refunded"], "delivered"),
        (["awaiting_input", "unpaid"], "awaiting_input"),
        (["queued", "awaiting_input"], "awaiting_input"),
        (["late", "replacement"], "late"),
        (["replacement", "queued"], "replacement"),
        (["expired", "cancelled"], "expired"),
        (["refunded", "cancelled"], "refunded"),
        ([], "cancelled"),
    ],
)
def test_one_ranking_puts_the_customers_own_move_first(states, lead):
    assert status.lead_state(states) == lead


def test_a_queued_item_past_its_delivery_time_reads_as_late():
    now = timezone.now()
    item = OrderItem(status=S.QUEUED, due_at=now - timedelta(minutes=1))
    assert status.item_state(item, None, now, now) == "late"
    item.due_at = None
    assert status.item_state(item, None, now, now) == "queued"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "warranty, span",
    [
        (Product.Warranty.DAYS_7, 3),
        (Product.Warranty.DAYS_7, 30),
        (Product.Warranty.DAYS_7, None),
        (Product.Warranty.FULL_PERIOD, 20),
    ],
)
def test_the_warranty_last_day_is_the_last_day_refund_amount_accepts(
    buyer, plan, operator, warranty, span
):
    Product.objects.filter(pk=plan.product_id).update(warranty=warranty)
    for offset, accepted in ((0, True), (1, False)):
        _, items = paid_order(buyer, operator, [services.OrderLine(plan, 1)])
        item = items[0]
        end = timezone.localdate() + timedelta(days=span) if span else None
        deliver(item, operator, expires_at=end)
        item.refresh_from_db()
        last = status.warranty_last_day(item)
        assert last is not None
        day = last + timedelta(days=offset)
        noon = timezone.make_aware(datetime.combine(day, time(12)))
        with time_machine.travel(noon, tick=False):
            claim(item, operator)
        item.refresh_from_db()
        if accepted:
            assert services.refund_amount(item, "warranty_refund", today=day) >= Decimal(0)
        else:
            with pytest.raises(ValidationError):
                services.refund_amount(item, "warranty_refund", today=day)

"""ADR-0009's input-timeout ladder: two notices, the operator's flag and the 21st-day end."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from queue import Queue
from threading import Event

import pytest
import time_machine
from django.db import DatabaseError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import Notification
from apps.orders import holiday, services
from apps.orders.admin import queue_overview
from apps.orders.clock import TEHRAN
from apps.orders.models import OrderItem, OrderItemEvent
from apps.orders.tests.test_delivery import queued
from apps.orders.tests.test_input_clock import act, purchase
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock
from apps.payments.models import Refund
from apps.payments.services import execute_refunds

pytestmark = pytest.mark.django_db(transaction=True)

# Monday 12:00 in Tehran: the reminder is due Wednesday 12:00, the operator's flag from
# 16 November, the final warning from 23 November, and the cancellation from 30 November
# (۹ آذر ۱۴۰۵), so the last day to send is 29 November (۸ آذر ۱۴۰۵).
REQUESTED = datetime(2026, 11, 9, 12, tzinfo=TEHRAN)
LAST_DAY = "۸ آذر ۱۴۰۵"
CANCEL_DAY = "۹ آذر ۱۴۰۵"
STALE = "بیش از ۷ روز منتظر اطلاعات"


def tehran(*args):
    return datetime(*args, tzinfo=TEHRAN)


def run(when):
    with time_machine.travel(when, tick=False):
        return services.run_input_ladder()


def waiting(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, at=REQUESTED)
    assert item.status == "AWAITING_INPUT" and item.input_requested_at == REQUESTED
    return item


def notices(item, template):
    return list(Notification.objects.filter(order_id=item.order_id, template_name=template))


# The two notices


def test_the_reminder_goes_48_hours_after_the_request_and_only_once(buyer, plan, operator):
    item = waiting(buyer, plan, operator)
    events = item.events.count()
    assert run(REQUESTED + timedelta(hours=47, minutes=59)) == 0
    assert run(REQUESTED + timedelta(hours=48)) == 1
    assert run(REQUESTED + timedelta(hours=49)) == 0
    item.refresh_from_db()
    assert item.input_notice == OrderItem.InputNotice.REMINDER
    [reminder] = notices(item, "input_reminder")
    assert LAST_DAY in reminder.text_body and LAST_DAY in reminder.html_body
    # A notice changes no state, so a page the operator already opened stays valid.
    assert item.events.count() == events


def test_the_final_warning_names_the_last_day_from_the_14th_tehran_day(buyer, plan, operator):
    item = waiting(buyer, plan, operator)
    run(REQUESTED + timedelta(hours=48))
    assert run(tehran(2026, 11, 22, 21, 59)) == 0
    assert run(tehran(2026, 11, 23, 10)) == 1
    assert run(tehran(2026, 11, 23, 11)) == 0
    [warning] = notices(item, "input_final_warning")
    assert LAST_DAY in warning.text_body and LAST_DAY in warning.subject + warning.html_body
    item.refresh_from_db()
    assert item.input_notice == OrderItem.InputNotice.FINAL


def test_a_late_sweep_sends_only_the_notice_now_due(buyer, plan, operator):
    item = waiting(buyer, plan, operator)
    assert run(tehran(2026, 11, 24, 10)) == 1
    assert notices(item, "input_reminder") == []
    assert len(notices(item, "input_final_warning")) == 1


def test_a_customer_linked_to_telegram_hears_the_last_day_there_too(
    buyer, plan, operator, settings
):
    settings.TELEGRAM_BOT_TOKEN = "test-bot-token"  # noqa: S105 - a fake token, never sent
    type(buyer).objects.filter(pk=buyer.pk).update(telegram_id=4242)
    item = waiting(buyer, plan, operator)
    requests = notices(item, "input_requested")
    assert {row.channel for row in requests} == {"email", "telegram"}
    for request in requests:
        assert f"تا پایان {LAST_DAY}" in request.text_body
        if request.channel == Notification.Channel.EMAIL:
            assert f"تا پایان {LAST_DAY}" in request.html_body
    run(tehran(2026, 11, 23, 10))
    [telegram] = Notification.objects.filter(
        order_id=item.order_id, template_name="input_final_warning", channel="telegram"
    )
    assert LAST_DAY in telegram.text_body


@pytest.mark.parametrize("hour,minute", [(9, 59), (22, 0)])
def test_the_ladder_acts_only_while_support_is_open(buyer, plan, operator, hour, minute):
    item = waiting(buyer, plan, operator)
    assert run(tehran(2026, 11, 30, hour, minute)) == 0
    item.refresh_from_db()
    assert (item.status, item.input_notice) == ("AWAITING_INPUT", OrderItem.InputNotice.NONE)


# The 21st day


def test_the_21st_tehran_day_cancels_with_an_owed_refund_and_claims_no_transfer(
    buyer, plan, operator
):
    plan.stock_quantity = 5
    item = waiting(buyer, plan, operator)
    run(tehran(2026, 11, 23, 10))
    assert run(tehran(2026, 11, 29, 21, 59)) == 0
    assert run(tehran(2026, 11, 30, 21)) == 1
    events = item.events.count()
    assert run(tehran(2026, 11, 30, 21, 30)) == 0
    assert item.events.count() == events
    item.refresh_from_db()
    assert (item.status, item.cancel_reason) == ("CANCELLED", "input_timeout")
    refund = Refund.objects.get(order_item=item)
    assert (refund.amount, refund.status, refund.created_by) == (
        item.paid_amount,
        Refund.Status.OWED,
        None,
    )
    assert (refund.executed_by, refund.executed_at, refund.bank_ref) == (None, None, "")
    cancellation = item.events.latest("pk")
    assert (cancellation.from_status, cancellation.to_status) == ("AWAITING_INPUT", "CANCELLED")
    assert (cancellation.actor, cancellation.actor_user) == ("system", None)
    assert "۲۱ روز" in cancellation.note
    plan.refresh_from_db()
    assert plan.stock_quantity == 5
    [notice] = notices(item, "item_cancelled")
    assert "۲۱ روز" in notice.text_body and "۲۱ روز" in notice.html_body
    assert (
        "پس از دریافت شماره‌ی کارت یا شبا، مبلغ را تا یک روز کاری واریز می‌کنیم" in notice.text_body
    )


def test_a_holiday_moves_no_rung_of_the_ladder(buyer, plan, operator):
    item = waiting(buyer, plan, operator)
    with time_machine.travel(REQUESTED + timedelta(hours=1), tick=False):
        holiday.start(actor=operator, expected_latest=None)
    assert run(REQUESTED + timedelta(hours=48)) == 1
    with time_machine.travel(tehran(2026, 11, 16, 12, 1), tick=False):
        tiles = {tile["label"]: tile["count"] for tile in queue_overview(operator)}
    assert tiles[STALE] == 1
    assert run(tehran(2026, 11, 23, 10)) == 1
    assert run(tehran(2026, 11, 30, 20, 59)) == 0
    assert run(tehran(2026, 11, 30, 21)) == 1
    item.refresh_from_db()
    assert (item.status, item.cancel_reason) == ("CANCELLED", "input_timeout")
    assert item.events.filter(to_status="CANCELLED").count() == 1
    assert Refund.objects.filter(order_item=item, status=Refund.Status.OWED).count() == 1


@pytest.mark.parametrize("hour,minute", [(10, 0), (20, 59)])
def test_the_21st_day_leaves_its_support_hours_to_record_late_details(
    buyer, plan, operator, hour, minute
):
    # Details sent to support late on the last day are recorded by hand the next morning.
    item = waiting(buyer, plan, operator)
    assert run(tehran(2026, 11, 30, hour, minute)) == 0
    item.refresh_from_db()
    assert item.status == "AWAITING_INPUT"
    assert notices(item, "input_final_warning") == []  # no notice names a day already past
    assert run(tehran(2026, 11, 30, 21)) == 1
    item.refresh_from_db()
    assert item.status == "CANCELLED"


def test_a_later_request_starts_a_new_ladder_that_a_paused_deadline_does_not_stop(
    buyer, plan, operator
):
    item = waiting(buyer, plan, operator)
    run(REQUESTED + timedelta(hours=48))
    with time_machine.travel(tehran(2026, 11, 12, 12), tick=False):
        act(services.receive_input, item, operator)
    second = tehran(2026, 11, 20, 12)
    with time_machine.travel(second, tick=False):
        act(services.request_input, item, operator)
    item.refresh_from_db()
    assert item.sla_paused_at == second  # the delivery deadline is paused; the ladder is not
    assert item.input_notice == OrderItem.InputNotice.NONE
    assert run(tehran(2026, 11, 22, 11, 59)) == 0
    assert run(tehran(2026, 11, 22, 12)) == 1
    assert len(notices(item, "input_reminder")) == 2
    assert run(tehran(2026, 11, 30, 21)) == 0  # the first episode's 21st day
    item.refresh_from_db()
    assert item.status == "AWAITING_INPUT"
    assert run(tehran(2026, 12, 10, 10)) == 1  # the second episode's final warning
    assert run(tehran(2026, 12, 11, 21)) == 1
    item.refresh_from_db()
    assert (item.status, item.cancel_reason) == ("CANCELLED", "input_timeout")


def test_a_selection_from_an_older_episode_does_nothing(buyer, plan, operator):
    item = waiting(buyer, plan, operator)
    with time_machine.travel(tehran(2026, 11, 12, 12), tick=False):
        act(services.receive_input, item, operator)
    with time_machine.travel(tehran(2026, 11, 13, 12), tick=False):
        act(services.request_input, item, operator)
    with time_machine.travel(tehran(2026, 11, 30, 21), tick=False):
        assert (
            services.advance_input_ladder(
                item_id=item.pk, requested_at=REQUESTED, now=timezone.now()
            )
            == ""
        )
    item.refresh_from_db()
    assert (item.status, item.input_notice) == ("AWAITING_INPUT", OrderItem.InputNotice.NONE)


# Races


def _hold(monkeypatch):
    original = services._input_event
    entered, release = Event(), Event()

    def held(*args, **kwargs):
        event = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return event

    monkeypatch.setattr(services, "_input_event", held)
    return entered, release


def test_details_received_during_the_sweep_win_over_the_cancellation(
    buyer, plan, operator, monkeypatch
):
    item = waiting(buyer, plan, operator)
    entered, release = _hold(monkeypatch)
    pids = Queue()
    with time_machine.travel(tehran(2026, 11, 30, 21), tick=False):
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipt = pool.submit(
                in_connection, pids, lambda: act(services.receive_input, item, operator)
            )
            holder = pids.get(timeout=10)
            assert entered.wait(10)
            sweep = pool.submit(in_connection, pids, services.run_input_ladder)
            waiter = pids.get(timeout=10)
            try:
                wait_for_lock(waiter, holder)
            finally:
                release.set()
            assert receipt.result(timeout=10).status == "QUEUED"
            assert sweep.result(timeout=10) == 0
    assert not Refund.objects.filter(order_item=item).exists()


def test_two_sweeps_at_once_cancel_once(buyer, plan, operator, monkeypatch):
    item = waiting(buyer, plan, operator)
    entered, release = _hold(monkeypatch)
    pids = Queue()
    with time_machine.travel(tehran(2026, 11, 30, 21), tick=False):
        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(in_connection, pids, services.run_input_ladder)
            holder = pids.get(timeout=10)
            assert entered.wait(10)
            contender = pool.submit(in_connection, pids, services.run_input_ladder)
            waiter = pids.get(timeout=10)
            try:
                wait_for_lock(waiter, holder)
            finally:
                release.set()
            assert sorted([leader.result(timeout=10), contender.result(timeout=10)]) == [0, 1]
    assert item.events.filter(to_status="CANCELLED").count() == 1
    assert Refund.objects.filter(order_item=item).count() == 1


# Who owes a refund


def _cancelled_by_hand(buyer, plan, operator, reason, by=None):
    """A cancelled item, with its history row by `by`, or by the system when None."""
    _, item = queued(buyer, plan, operator)
    OrderItem.objects.filter(pk=item.pk).update(status="CANCELLED", cancel_reason=reason)
    OrderItemEvent.objects.create(
        order_item=item,
        from_status="QUEUED",
        to_status="CANCELLED",
        actor="system" if by is None else "operator",
        actor_user=by,
    )
    return item


@pytest.mark.parametrize(
    "reason,by_operator,allowed",
    [
        ("supply_failure", False, False),
        ("input_timeout", True, False),  # the operator's own 21-day cancellation
        ("input_timeout", False, True),
    ],
)
def test_only_the_systems_21_day_cancellation_may_owe_a_refund_without_a_person(
    buyer, plan, operator, reason, by_operator, allowed
):
    item = _cancelled_by_hand(buyer, plan, operator, reason, operator if by_operator else None)

    def owe():
        Refund.objects.create(order_item=item, amount=Decimal("1000"), created_by=None)

    if allowed:
        owe()
    else:
        with pytest.raises(DatabaseError, match="without a person"), transaction.atomic():
            owe()


def test_a_system_refund_keeps_its_attribution_and_is_paid_by_a_person(buyer, plan, operator):
    item = _cancelled_by_hand(buyer, plan, operator, "input_timeout")
    refund = Refund.objects.create(order_item=item, amount=Decimal("1000"), created_by=None)
    with pytest.raises(DatabaseError, match="never changes"), transaction.atomic():
        Refund.objects.filter(pk=refund.pk).update(created_by=operator)
    execute_refunds(
        order_id=item.order_id,
        refund_ids=[refund.pk],
        actor=operator,
        amount=Decimal("1000"),
        destination_kind="card",
        destination_last4="1234",
        bank_ref="BANK-1",
        executed_at=timezone.now(),
    )
    refund.refresh_from_db()
    assert (refund.status, refund.created_by, refund.executed_by) == ("executed", None, operator)


# The operator's flag


def test_the_panel_flags_a_wait_of_more_than_seven_days(buyer, plan, operator, client):
    waiting(buyer, plan, operator)

    def stale():
        return next(tile for tile in queue_overview(operator) if tile["label"] == STALE)

    # The tile says «بیش از ۷ روز», so the 7th calendar day alone is not enough.
    for moment in (tehran(2026, 11, 16, 0, 0), REQUESTED + timedelta(days=7)):
        with time_machine.travel(moment, tick=False):
            assert (stale()["count"], stale()["tone"]) == (0, "closed")
    with time_machine.travel(REQUESTED + timedelta(days=7, minutes=1), tick=False):
        assert (stale()["count"], stale()["tone"]) == (1, "problem")
        assert stale()["url"].endswith("?tab=waiting")
        client.force_login(operator)
        page = client.get(reverse("admin:orders_order_changelist") + "?tab=waiting")
    assert f'data-clock="overdue">لغو خودکار: {CANCEL_DAY}' in page.content.decode()


def test_the_sweep_is_a_periodic_task():
    from apps.orders.tasks import climb_input_ladder

    with time_machine.travel(tehran(2026, 11, 30, 3), tick=False):
        assert climb_input_ladder.call_local() == 0


def test_the_cancellation_date_label_follows_the_panel_language(buyer, plan, operator, client):
    waiting(buyer, plan, operator)
    client.force_login(operator)
    client.post(reverse("set_language"), {"language": "en", "next": "/admin/"})
    page = client.get(reverse("admin:orders_order_changelist") + "?tab=waiting")
    assert "Automatic cancellation:" in page.content.decode()
    assert "لغو خودکار" not in page.content.decode()


def test_a_request_after_midnight_counts_from_its_tehran_date(buyer, plan, operator):
    # 01:00 on 10 November in Tehran is still 9 November in UTC.
    _, item = purchase(buyer, plan, operator, at=tehran(2026, 11, 10, 1))
    run(tehran(2026, 11, 23, 21, 59))
    assert notices(item, "input_final_warning") == []
    assert run(tehran(2026, 11, 24, 10)) == 1
    [warning] = notices(item, "input_final_warning")
    assert CANCEL_DAY in warning.text_body  # the last day to send is 30 November
    assert run(tehran(2026, 11, 30, 21, 30)) == 0
    assert run(tehran(2026, 12, 1, 21)) == 1

"""A delay notice promises a new time; anything that happens to its item after it makes that
time untrue, so the notice is never sent: not at creation, not on a retry, not by a sweep."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event

import pytest
from django.core import mail
from django.db import transaction
from django.utils import timezone

from apps.notifications import services
from apps.notifications.models import Notification
from apps.notifications.tests.test_telegram_messages import linked
from apps.notifications.tests.test_telegram_messages import telegram as telegram
from apps.orders import holiday
from apps.orders import services as orders
from apps.orders.models import Holiday
from apps.orders.tests.test_delivery import queued
from apps.orders.tests.test_input_clock import act
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock

pytestmark = pytest.mark.django_db(transaction=True)
NOTE = "تأمین‌کننده دیر کرد"


def delayed(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    act(orders.delay_supply, item, operator, hours=2, note=NOTE)
    # Only the delay is left to send, so every count below is about it.
    Notification.objects.exclude(template_name="supply_delayed").update(status="sent")
    return item


def delay_rows():
    return Notification.objects.filter(template_name="supply_delayed")


def send_all():
    return services.send_due_notifications() + services.send_due_telegram()


def a_holiday_comes_and_goes(operator):
    opened = holiday.start(actor=operator, expected_latest=None)
    holiday.end(actor=operator, expected_open=opened.pk)


def assert_dropped():
    for row in delay_rows():
        assert (row.status, row.last_error_class, row.attempts) == ("failed", "superseded", 0)
        assert row.next_attempt_at is None and row.sent_at is None


@pytest.fixture
def both_channels(buyer, telegram):
    linked(buyer)
    return telegram


def test_a_holiday_drops_a_pending_delay_from_both_channels(buyer, plan, operator, both_channels):
    item = delayed(buyer, plan, operator)
    assert delay_rows().count() == 2
    a_holiday_comes_and_goes(operator)
    assert send_all() == 0
    assert mail.outbox == [] and both_channels.sent == []
    assert_dropped()
    item.refresh_from_db()
    assert item.sla_paused_at is None


def test_a_details_round_trip_drops_a_pending_delay(buyer, plan, operator, both_channels):
    item = delayed(buyer, plan, operator)
    act(orders.request_input, item, operator)
    Notification.objects.exclude(template_name="supply_delayed").update(status="sent")
    act(orders.resume_input, item, operator)
    assert send_all() == 0
    assert mail.outbox == [] and both_channels.sent == []
    assert_dropped()


def test_a_delay_after_the_holiday_still_goes(buyer, plan, operator, both_channels):
    _, item = queued(buyer, plan, operator)
    a_holiday_comes_and_goes(operator)
    act(orders.delay_supply, item, operator, hours=2, note=NOTE)
    Notification.objects.exclude(template_name="supply_delayed").update(status="sent")
    assert send_all() == 2
    assert len(mail.outbox) == 1 and len(both_channels.sent) == 1


def test_a_notice_created_after_a_later_event_has_no_rows(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    act(orders.delay_supply, item, operator, hours=2, note=NOTE)
    occurrence = item.events.latest("pk")
    delay_rows().delete()
    a_holiday_comes_and_goes(operator)
    services.enqueue_event(
        "supply_delayed", occurrence.pk, item.order_id, item_id=item.pk, due_at=item.due_at
    )
    assert not delay_rows().exists()


def test_the_minute_sweep_drops_a_superseded_delay_before_its_retry(buyer, plan, operator):
    item = delayed(buyer, plan, operator)
    later = timezone.now() + timedelta(hours=6)
    delay_rows().update(attempts=4, next_attempt_at=later)
    fresh = Notification.objects.create(
        order_id=item.order_id,
        dedupe_key="supply_delayed:999999999:user:1:email",
        template_name="supply_delayed",
        recipient="buyer@example.test",
        subject="s",
        text_body="t",
        html_body="h",
        next_attempt_at=later,
    )
    a_holiday_comes_and_goes(operator)
    assert services.drop_superseded_delay_notices() == 1
    row = delay_rows().exclude(pk=fresh.pk).get()
    assert (row.status, row.last_error_class, row.attempts) == ("failed", "superseded", 4)
    fresh.refresh_from_db()
    assert fresh.status == "pending"


# Who wins: the send or the pause


def blocking_sends(monkeypatch, entered, release):
    original = services.send_templated_email

    def held_send(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(services, "send_templated_email", held_send)


def test_a_send_already_under_way_finishes_and_the_switch_waits(buyer, plan, operator, monkeypatch):
    item = delayed(buyer, plan, operator)
    entered, release, pids = Event(), Event(), Queue()
    blocking_sends(monkeypatch, entered, release)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sender = pool.submit(in_connection, pids, services.send_due_notifications)
        holder = pids.get(timeout=5)
        assert entered.wait(10)
        switch = pool.submit(
            in_connection, pids, lambda: holiday.start(actor=operator, expected_latest=None)
        )
        wait_for_lock(pids.get(timeout=5), holder)
        release.set()
        assert sender.result(timeout=15) == 1
        assert switch.result(timeout=15) is not None
    assert len(mail.outbox) == 1
    item.refresh_from_db()
    assert item.sla_paused_at is not None


def test_a_pause_that_commits_first_stops_the_send(buyer, plan, operator):
    item = delayed(buyer, plan, operator)
    done, release, pids = Event(), Event(), Queue()

    def held_start():
        with transaction.atomic():
            opened = holiday.start(actor=operator, expected_latest=None)
            done.set()
            assert release.wait(10)
        return opened

    with ThreadPoolExecutor(max_workers=2) as pool:
        switch = pool.submit(in_connection, pids, held_start)
        holder = pids.get(timeout=5)
        assert done.wait(10)
        sender = pool.submit(in_connection, pids, services.send_due_notifications)
        wait_for_lock(pids.get(timeout=5), holder)
        release.set()
        assert switch.result(timeout=15) is not None
        assert sender.result(timeout=15) == 0
    assert mail.outbox == []
    item.refresh_from_db()
    assert item.sla_paused_at is not None
    assert_dropped()


def test_a_telegram_send_under_way_finishes_and_the_switch_waits(
    buyer, plan, operator, both_channels
):
    delayed(buyer, plan, operator)
    delay_rows().filter(channel="email").update(status="sent")
    entered, release, pids = Event(), Event(), Queue()
    original = services.telegram.send_message

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    services.telegram.send_message = held
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            sender = pool.submit(in_connection, pids, services.send_due_telegram)
            holder = pids.get(timeout=5)
            assert entered.wait(10)
            switch = pool.submit(
                in_connection, pids, lambda: holiday.start(actor=operator, expected_latest=None)
            )
            wait_for_lock(pids.get(timeout=5), holder)
            release.set()
            assert sender.result(timeout=15) == 1
            assert switch.result(timeout=15) is not None
    finally:
        services.telegram.send_message = original
    assert len(both_channels.sent) == 1


def test_a_pause_that_commits_first_stops_the_telegram_send(buyer, plan, operator, both_channels):
    delayed(buyer, plan, operator)
    delay_rows().filter(channel="email").update(status="sent")
    done, release, pids = Event(), Event(), Queue()

    def held_start():
        with transaction.atomic():
            opened = holiday.start(actor=operator, expected_latest=None)
            done.set()
            assert release.wait(10)
        return opened

    with ThreadPoolExecutor(max_workers=2) as pool:
        switch = pool.submit(in_connection, pids, held_start)
        holder = pids.get(timeout=5)
        assert done.wait(10)
        sender = pool.submit(in_connection, pids, services.send_due_telegram)
        wait_for_lock(pids.get(timeout=5), holder)
        release.set()
        assert switch.result(timeout=15) is not None
        assert sender.result(timeout=15) == 0
    assert both_channels.sent == []
    row = delay_rows().get(channel="telegram")
    assert (row.status, row.last_error_class, row.attempts) == ("failed", "superseded", 0)


def test_a_delay_committed_as_the_holiday_starts_is_never_sent(
    buyer, plan, operator, both_channels
):
    _, item = queued(buyer, plan, operator)
    Notification.objects.update(status="sent")
    done, release, pids = Event(), Event(), Queue()

    def held_delay():
        with transaction.atomic():
            moved = act(orders.delay_supply, item, operator, hours=2, note=NOTE)
            done.set()
            assert release.wait(10)
        return moved

    with ThreadPoolExecutor(max_workers=2) as pool:
        delay = pool.submit(in_connection, pids, held_delay)
        holder = pids.get(timeout=5)
        assert done.wait(10)
        switch = pool.submit(
            in_connection, pids, lambda: holiday.start(actor=operator, expected_latest=None)
        )
        wait_for_lock(pids.get(timeout=5), holder)
        release.set()
        assert delay.result(timeout=15) is not None
        assert switch.result(timeout=15) is not None
    assert Holiday.objects.filter(ended_at=None).exists()
    assert send_all() == 0
    assert mail.outbox == [] and both_channels.sent == []

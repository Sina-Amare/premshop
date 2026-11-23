"""The owner's holiday: running delivery clocks stop, and restart moved by the stopped time."""

from datetime import datetime, timedelta

import pytest
import time_machine
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.catalog.models import Plan
from apps.orders import holiday, selectors, services
from apps.orders.clock import TEHRAN, resumed_deadline
from apps.orders.models import Holiday, OrderItem, OrderItemEvent
from apps.orders.tests.test_delivery import deliver
from apps.orders.tests.test_input_clock import act, purchase
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = pytest.mark.django_db


def tehran(*args):
    return datetime(*args, tzinfo=TEHRAN)


MONDAY = tehran(2026, 11, 9, 10)


def start(actor, at):
    with time_machine.travel(at, tick=False):
        latest = Holiday.objects.order_by("-pk").values_list("pk", flat=True).first()
        return holiday.start(actor=actor, expected_latest=latest)


def end(actor, at):
    with time_machine.travel(at, tick=False):
        return holiday.end(actor=actor, expected_open=Holiday.objects.get(ended_at=None).pk)


def customer(name):
    return User.objects.create_user(f"{name}@example.test")


# The arithmetic


@pytest.mark.parametrize(
    ("due", "paused", "resumed", "expected"),
    [
        # Two hours late when paused; the shift lands at 08:00, before support opens.
        (
            tehran(2026, 11, 10, 10),
            tehran(2026, 11, 10, 12),
            tehran(2026, 11, 11, 10),
            tehran(2026, 11, 11, 10),
        ),
        (
            tehran(2026, 11, 9, 16),
            tehran(2026, 11, 9, 12),
            tehran(2026, 11, 9, 17, 59),
            tehran(2026, 11, 9, 21, 59),
        ),
        # 22:00 is outside [10:00, 22:00): the next opening.
        (
            tehran(2026, 11, 9, 16),
            tehran(2026, 11, 9, 12),
            tehran(2026, 11, 9, 18),
            tehran(2026, 11, 10, 10),
        ),
    ],
)
def test_a_resumed_deadline_moves_by_the_stopped_time_then_into_support_hours(
    due, paused, resumed, expected
):
    assert resumed_deadline(due, paused, resumed) == expected


# Starting and ending


def test_starting_pauses_running_clocks_and_leaves_input_waits_alone(buyer, plan, operator):
    _, running = purchase(buyer, plan, operator, telegram=False)
    _, waiting = purchase(customer("waiting"), plan, operator, telegram=False)
    with time_machine.travel(MONDAY + timedelta(hours=1), tick=False):
        act(services.request_input, waiting, operator)
    waiting.refresh_from_db()
    asked, stopped = waiting.input_requested_at, waiting.sla_paused_at
    began = MONDAY + timedelta(hours=2)
    opened = start(operator, began)
    running.refresh_from_db()
    waiting.refresh_from_db()
    assert opened.started_at == began and opened.started_by == operator
    assert running.status == "QUEUED" and running.sla_paused_at == began
    assert waiting.status == "AWAITING_INPUT"
    assert (waiting.input_requested_at, waiting.sla_paused_at) == (asked, stopped)
    pause = running.events.latest("pk")
    assert (pause.from_status, pause.to_status, pause.actor) == ("QUEUED", "QUEUED", "system")
    assert pause.actor_user is None and pause.holiday == opened and pause.note
    assert not waiting.events.filter(holiday__isnull=False).exists()


def test_ending_moves_each_paused_deadline_once_and_records_it(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, telegram=False)
    due = item.due_at
    start(operator, MONDAY + timedelta(hours=2))
    closed = end(operator, MONDAY + timedelta(days=1, hours=2))
    item.refresh_from_db()
    assert closed.ended_by == operator and closed.ended_at == MONDAY + timedelta(days=1, hours=2)
    assert item.sla_paused_at is None
    assert item.due_at == resumed_deadline(due, MONDAY + timedelta(hours=2), closed.ended_at)
    resume = item.events.latest("pk")
    assert (resume.from_status, resume.to_status, resume.actor) == ("QUEUED", "QUEUED", "system")
    assert resume.holiday == closed and resume.actor_user is None
    assert item.events.filter(holiday=closed).count() == 2


def test_a_payment_during_a_holiday_starts_paused(buyer, plan, operator):
    began = MONDAY - timedelta(hours=1)
    start(operator, began)
    _, item = purchase(buyer, plan, operator, telegram=False)
    assert item.status == "QUEUED" and item.paid_at == MONDAY
    assert item.sla_paused_at == MONDAY and item.due_at == MONDAY + timedelta(hours=6)
    closed = end(operator, MONDAY + timedelta(hours=3))
    item.refresh_from_db()
    assert item.due_at == MONDAY + timedelta(hours=9) and item.sla_paused_at is None
    assert item.events.filter(holiday=closed).count() == 1


def test_a_first_wait_paid_during_a_holiday_has_no_clock_to_pause(buyer, plan, operator):
    start(operator, MONDAY - timedelta(hours=1))
    _, item = purchase(buyer, plan, operator, telegram=True)
    assert item.status == "AWAITING_INPUT" and item.due_at is None and item.sla_paused_at is None


def test_details_received_during_a_holiday_start_the_clock_paused(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, telegram=True)
    Plan.objects.filter(pk=plan.pk).update(telegram_required=False)
    start(operator, MONDAY + timedelta(hours=1))
    received = MONDAY + timedelta(hours=2)
    with time_machine.travel(received, tick=False):
        act(services.receive_input, item, operator)
    item.refresh_from_db()
    assert item.status == "QUEUED" and item.sla_paused_at == received
    assert item.due_at == received + timedelta(hours=6)
    end(operator, received + timedelta(hours=1))
    item.refresh_from_db()
    assert item.due_at == received + timedelta(hours=7) and item.sla_paused_at is None


def test_a_wait_that_ends_during_a_holiday_is_shifted_once_at_its_end(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, telegram=False)
    due = item.due_at
    asked = MONDAY + timedelta(hours=1)
    with time_machine.travel(asked, tick=False):
        act(services.request_input, item, operator)
    start(operator, MONDAY + timedelta(hours=2))
    with time_machine.travel(MONDAY + timedelta(hours=3), tick=False):
        act(services.resume_input, item, operator)
    item.refresh_from_db()
    assert item.status == "QUEUED" and item.sla_paused_at == asked and item.due_at == due
    resumed = MONDAY + timedelta(hours=5)
    end(operator, resumed)
    item.refresh_from_db()
    assert item.due_at == resumed_deadline(due, asked, resumed) and item.sla_paused_at is None


def test_asking_a_paused_item_for_details_keeps_the_time_it_stopped(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, telegram=False)
    due = item.due_at
    began = MONDAY + timedelta(hours=1)
    start(operator, began)
    item.refresh_from_db()
    with time_machine.travel(began + timedelta(hours=1), tick=False):
        act(services.request_input, item, operator)
    item.refresh_from_db()
    assert item.status == "AWAITING_INPUT" and item.sla_paused_at == began
    assert item.input_requested_at == began + timedelta(hours=1)
    end(operator, began + timedelta(hours=2))
    item.refresh_from_db()
    assert item.sla_paused_at == began and item.due_at == due
    received = began + timedelta(hours=3)
    with time_machine.travel(received, tick=False):
        act(services.resume_input, item, operator)
    item.refresh_from_db()
    assert item.due_at == resumed_deadline(due, began, received)


# What the customer reads


def test_the_customer_never_reads_a_holidays_own_lines(buyer, plan, operator):
    order, item = purchase(buyer, plan, operator, telegram=False)
    start(operator, MONDAY + timedelta(hours=1))
    end(operator, MONDAY + timedelta(hours=2))
    moved = "زمان تحویل تغییر کرد"

    def read():
        page = [row["status"] for row in selectors.customer_timeline(order)]
        tracking = [
            row["status"]
            for group in selectors.order_for_tracking(order.tracking_token)
            for row in group["timeline"]
        ]
        return page, tracking

    page, tracking = read()
    assert moved not in page and moved not in tracking
    with time_machine.travel(MONDAY + timedelta(hours=3), tick=False):
        act(services.delay_supply, item, operator, hours=2, note="تأمین‌کننده دیر کرد")
    page, tracking = read()
    assert moved in page and moved in tracking


# Delivering during a holiday


def test_a_paused_item_can_be_delivered_during_a_holiday(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, telegram=False)
    start(operator, MONDAY + timedelta(hours=1))
    with time_machine.travel(MONDAY + timedelta(hours=2), tick=False):
        deliver(item, operator)
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.sla_paused_at is None
    due = item.due_at
    closed = end(operator, MONDAY + timedelta(hours=3))
    item.refresh_from_db()
    # Only the pause: a delivered item is neither shifted nor given a resume line.
    assert item.events.filter(holiday=closed).count() == 1 and item.due_at == due


def test_a_paused_queued_item_outside_a_holiday_still_cannot_be_delivered(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, telegram=False)
    OrderItem.objects.filter(pk=item.pk).update(sla_paused_at=MONDAY)
    with pytest.raises(ValidationError):
        deliver(item, operator)


def test_a_paused_item_cannot_have_its_deadline_pushed(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, telegram=False)
    start(operator, MONDAY + timedelta(hours=1))
    with pytest.raises(ValidationError):
        act(services.delay_supply, item, operator, hours=2, note="تأمین‌کننده دیر کرد")


# Refusals


def test_a_second_start_and_an_end_without_a_holiday_are_refused(operator):
    start(operator, MONDAY)
    with pytest.raises(ValidationError), time_machine.travel(MONDAY, tick=False):
        holiday.start(actor=operator, expected_latest=Holiday.objects.get().pk)
    end(operator, MONDAY + timedelta(hours=1))
    with pytest.raises(ValidationError):
        holiday.end(actor=operator, expected_open=Holiday.objects.get().pk)


def test_a_form_for_an_earlier_holiday_cannot_end_or_start_another(operator):
    first = start(operator, MONDAY)
    end(operator, MONDAY + timedelta(hours=1))
    second = start(operator, MONDAY + timedelta(hours=2))
    with pytest.raises(ValidationError):
        holiday.end(actor=operator, expected_open=first.pk)
    end(operator, MONDAY + timedelta(hours=3))
    # A start form opened before `second` replayed after it ended.
    with pytest.raises(ValidationError):
        holiday.start(actor=operator, expected_latest=first.pk)
    assert Holiday.objects.filter(ended_at=None).count() == 0
    assert Holiday.objects.get(pk=second.pk).ended_at is not None


def test_only_the_owner_or_staff_given_the_permission_may_switch():
    staff = User.objects.create_user("staff@example.test", is_staff=True)
    staff.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="orders", codename__in=["change_order", "change_orderitem"]
        )
    )
    with pytest.raises(PermissionDenied):
        holiday.start(actor=staff, expected_latest=None)
    staff.user_permissions.add(Permission.objects.get(codename="change_holiday"))
    staff = User.objects.get(pk=staff.pk)
    opened = holiday.start(actor=staff, expected_latest=None)
    User.objects.filter(pk=staff.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        holiday.end(actor=staff, expected_open=opened.pk)


def test_the_database_admits_one_open_holiday(operator):
    Holiday.objects.create(started_by=operator)
    with pytest.raises(IntegrityError), transaction.atomic():
        Holiday.objects.create(started_by=operator)


def test_switching_never_reads_a_customers_details(buyer, plan, operator, monkeypatch):
    _, item = purchase(buyer, plan, operator, telegram=False)
    from apps.core import fields

    def refuse(*args, **kwargs):
        raise AssertionError("customer details were decrypted")

    monkeypatch.setattr(fields.EncryptedTextField, "from_db_value", refuse)
    start(operator, MONDAY + timedelta(hours=1))
    end(operator, MONDAY + timedelta(hours=2))
    assert OrderItemEvent.objects.filter(order_item_id=item.pk, holiday__isnull=False).count() == 2

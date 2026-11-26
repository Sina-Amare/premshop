"""The holiday switch against everything that starts, stops or ends a delivery clock at once."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event

import pytest
import time_machine
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Plan, Product
from apps.orders import holiday, services
from apps.orders.clock import delivery_deadline, resumed_deadline
from apps.orders.models import Holiday, Order
from apps.orders.tests.test_delivery import deliver
from apps.orders.tests.test_input_clock import act, purchase
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock
from apps.orders.tests.test_paid_cancellation import cancel
from apps.payments.services import record_manual_payment

pytestmark = pytest.mark.django_db(transaction=True)


def unpaid(buyer, plan):
    Product.objects.filter(pk=plan.product_id).update(delivery_hours=6)
    Plan.objects.filter(pk=plan.pk).update(
        telegram_required=False, input_type="none", max_quantity=1
    )
    plan.refresh_from_db()
    return services.place_order(user=buyer, lines=[services.OrderLine(plan, 1, None)])


def pay(order, operator):
    return lambda: record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="ref"
    )


def opened_now(operator):
    return holiday.start(actor=operator, expected_latest=None)


def held(operation, done, release):
    """Runs `operation`, then keeps its transaction and locks until `release`."""

    def run():
        with transaction.atomic():
            result = operation()
            done.set()
            assert release.wait(10)
        return result

    return run


def race(first, second, *, operator_waits=True):
    """`first` holds its locks; `second` must queue behind them before `first` commits."""
    done, release, pids = Event(), Event(), Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        holding = pool.submit(in_connection, pids, held(first, done, release))
        holder = pids.get(timeout=5)
        assert done.wait(10)
        waiting = pool.submit(in_connection, pids, second)
        waiter = pids.get(timeout=5)
        if operator_waits:
            wait_for_lock(waiter, holder)
        release.set()
        return holding.result(timeout=15), waiting.result(timeout=15)


def test_a_payment_racing_the_start_never_leaves_a_running_clock(buyer, plan, operator):
    order = unpaid(buyer, plan)
    opened, _ = race(lambda: opened_now(operator), pay(order, operator))
    item = order.items.get()
    assert item.status == "QUEUED" and item.paid_at >= opened.started_at
    assert item.sla_paused_at == item.paid_at


def test_an_order_waiting_for_the_holiday_switch_is_refused(buyer, plan, operator):
    def place():
        with pytest.raises(ValidationError) as refused:
            services.place_order(user=buyer, lines=[services.OrderLine(plan, 1)])
        assert refused.value.code == "holiday"

    race(lambda: opened_now(operator), place)
    assert not Order.objects.exists()
    assert Plan.objects.get(pk=plan.pk).stock_quantity == plan.stock_quantity


def test_a_payment_racing_the_end_keeps_its_whole_pause(buyer, plan, operator):
    order = unpaid(buyer, plan)
    opened = opened_now(operator)
    _, closed = race(
        pay(order, operator), lambda: holiday.end(actor=operator, expected_open=opened.pk)
    )
    item = order.items.get()
    stopped_from = item.paid_at
    due = delivery_deadline(paid_at=stopped_from, delivery_hours=6)
    assert item.sla_paused_at is None
    assert item.due_at == resumed_deadline(due, stopped_from, closed.ended_at)


def test_a_details_request_racing_the_start_loses_no_stopped_time(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator, telegram=False)
    due = item.due_at
    _, opened = race(
        lambda: act(services.request_input, item, operator), lambda: opened_now(operator)
    )
    item.refresh_from_db()
    assert item.status == "AWAITING_INPUT" and item.sla_paused_at <= opened.started_at
    holiday.end(actor=operator, expected_open=opened.pk)
    stopped_from = item.sla_paused_at
    item.refresh_from_db()
    assert item.sla_paused_at == stopped_from
    at = timezone.now() + timedelta(hours=1)
    with time_machine.travel(at, tick=False):
        resumed = act(services.resume_input, item, operator)
    assert resumed.due_at == resumed_deadline(due, stopped_from, at)


def test_details_a_payment_and_the_switch_on_one_buyer_never_deadlock(buyer, plan, operator):
    _, waiting = purchase(buyer, plan, operator, telegram=True)
    Plan.objects.filter(pk=plan.pk).update(telegram_required=False)
    second = unpaid(buyer, plan)
    done, release, pids = Event(), Event(), Queue()
    with ThreadPoolExecutor(max_workers=3) as pool:
        receipt = pool.submit(
            in_connection,
            pids,
            held(lambda: act(services.receive_input, waiting, operator), done, release),
        )
        holder = pids.get(timeout=5)
        assert done.wait(10)
        payment = pool.submit(in_connection, pids, pay(second, operator))
        wait_for_lock(pids.get(timeout=5), holder)
        switch = pool.submit(in_connection, pids, lambda: opened_now(operator))
        wait_for_lock(pids.get(timeout=5), holder)
        release.set()
        assert receipt.result(timeout=20) is not None
        assert payment.result(timeout=20) is not None
        assert switch.result(timeout=20) is not None
    waiting.refresh_from_db()
    paid = second.items.get()
    assert waiting.status == paid.status == "QUEUED"
    assert waiting.sla_paused_at is not None and paid.sla_paused_at is not None
    assert Holiday.objects.filter(ended_at=None).count() == 1


def test_a_delivery_racing_the_end_is_neither_moved_nor_resumed(buyer, plan, operator):
    order = unpaid(buyer, plan)
    pay(order, operator)()
    item = order.items.get()
    opened = opened_now(operator)
    item.refresh_from_db()
    due = item.due_at
    race(
        lambda: deliver(item, operator),
        lambda: holiday.end(actor=operator, expected_open=opened.pk),
    )
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.due_at == due and item.sla_paused_at is None
    assert item.events.filter(holiday=opened).count() == 1


def test_a_cancellation_racing_the_end_stays_cancelled_and_unmoved(buyer, plan, operator):
    order = unpaid(buyer, plan)
    pay(order, operator)()
    item = order.items.get()
    opened = opened_now(operator)
    item.refresh_from_db()
    due = item.due_at
    race(
        lambda: cancel(item, operator), lambda: holiday.end(actor=operator, expected_open=opened.pk)
    )
    item.refresh_from_db()
    assert item.status == "CANCELLED" and item.due_at == due
    assert item.events.filter(holiday=opened).count() == 1

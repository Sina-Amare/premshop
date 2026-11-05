from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event

import pytest

from apps.orders import services
from apps.orders.models import OrderItemEvent
from apps.orders.tests.test_input_clock import act, event_id, pay, purchase
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("operation", ["request", "receive", "resume"])
def test_input_action_races_serialize_without_duplicate_history(
    buyer, plan, operator, monkeypatch, operation
):
    _, item = purchase(buyer, plan, operator, telegram=operation == "receive")
    if operation == "resume":
        act(services.request_input, item, operator)
    expected = event_id(item)
    before = item.events.count()
    original = services._input_event
    entered, release, pids = Event(), Event(), Queue()

    def held(*args, **kwargs):
        original(*args, **kwargs)
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(services, "_input_event", held)
    service = getattr(services, f"{operation}_input")

    def submit():
        return act(service, item, operator, expected_event_id=expected)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(in_connection, pids, submit)
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        contender = pool.submit(in_connection, pids, submit)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        first, second = leader.result(timeout=10), contender.result(timeout=10)
    assert first.pk == second.pk
    assert first.due_at == second.due_at
    assert item.events.count() == before + 1


def test_receipt_waits_for_duplicate_payment_without_repeating_handoff(
    buyer, plan, operator, monkeypatch
):
    order, item = purchase(buyer, plan, operator)
    expected = event_id(item)
    from apps.payments import services as payments

    original = payments._payment_order
    entered, release, pids = Event(), Event(), Queue()

    def held(order_id):
        result = original(order_id)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(payments, "_payment_order", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        payment = pool.submit(in_connection, pids, lambda: pay(order, operator))
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        receipt = pool.submit(
            in_connection,
            pids,
            lambda: act(services.receive_input, item, operator, expected_event_id=expected),
        )
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert payment.result(timeout=10).status == "verified"
        assert receipt.result(timeout=10).status == "QUEUED"
    assert item.events.count() == 4
    assert OrderItemEvent.objects.filter(order_item=item, to_status="AWAITING_INPUT").count() == 1

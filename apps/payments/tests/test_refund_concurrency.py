from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event

import pytest

from apps.orders.models import OrderItemEvent
from apps.orders.services import OrderLine
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock
from apps.orders.tests.test_paid_cancellation import cancel, paid_order
from apps.payments import services
from apps.payments.models import Refund
from apps.payments.tests.test_refund_execution import execute

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("contender", ["repeat", "overlap", "cancellation"])
def test_refund_execution_serializes_at_the_shared_account_order_entry(
    buyer, plan, operator, monkeypatch, contender
):
    order, items = paid_order(buyer, operator, [OrderLine(plan, 3)])
    for item in items[:2]:
        cancel(item, operator)
    refunds = list(Refund.objects.order_by("pk"))
    entered, release, pids = Event(), Event(), Queue()
    original = services._payment_order

    def held(order_id):
        result = original(order_id)
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(services, "_payment_order", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(in_connection, pids, lambda: execute(order, refunds[:1], operator))
        holder = pids.get(timeout=10)
        assert entered.wait(10)

        def operation():
            if contender == "cancellation":
                return cancel(items[2], operator)
            return execute(order, refunds if contender == "overlap" else refunds[:1], operator)

        follower = pool.submit(in_connection, pids, operation)
        waiter = pids.get(timeout=10)
        try:
            # The leader has not read or updated any refund yet: only the entry locks can block.
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert leader.result(timeout=10) is not None
        second = follower.result(timeout=10)
    assert (second is not None) is (contender == "cancellation")
    assert Refund.objects.filter(status="executed").count() == 1
    assert Refund.objects.get(pk=refunds[1].pk).status == "owed"
    assert OrderItemEvent.objects.filter(to_status="REFUNDED").count() == 1
    if contender == "cancellation":
        assert Refund.objects.get(order_item=items[2]).status == "owed"

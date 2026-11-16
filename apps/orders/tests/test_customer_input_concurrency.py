from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event

import pytest
import time_machine

from apps.orders import services
from apps.orders.tests.test_customer_input import NEW, OLD, submit, waiting
from apps.orders.tests.test_input_clock import MONDAY, event_id
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock
from apps.payments.models import Refund

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("winner", ["submit", "cancel", "repeat"])
def test_customer_submission_serializes_with_cancellation_and_duplicate_posts(
    buyer, plan, operator, monkeypatch, winner
):
    assert callable(services.submit_customer_input)
    _, item = waiting(buyer, plan, operator)
    request = event_id(item)
    entered, release, pids = Event(), Event(), Queue()
    original = services._input_event

    def held(*args, **kwargs):
        event = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return event

    monkeypatch.setattr(services, "_input_event", held)

    def accept():
        return submit(item, buyer, expected_event_id=request)

    def cancel():
        return services.run_input_ladder()

    def repeat():
        return submit(item, buyer, "other@example.test", expected_event_id=request)

    leading, following = (
        (cancel, accept)
        if winner == "cancel"
        else (accept, repeat if winner == "repeat" else cancel)
    )
    with time_machine.travel(MONDAY + timedelta(days=21, hours=11), tick=False):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(in_connection, pids, leading)
            holder = pids.get(timeout=10)
            assert entered.wait(10)
            second = pool.submit(in_connection, pids, following)
            waiter = pids.get(timeout=10)
            try:
                wait_for_lock(waiter, holder)
            finally:
                release.set()
            first.result(timeout=10)
            second.result(timeout=10)
    item.refresh_from_db()
    assert item.events.count() == 4
    if winner == "cancel":
        assert (item.status, item.customer_input) == ("CANCELLED", OLD)
        assert Refund.objects.filter(order_item=item, status="owed").count() == 1
    else:
        assert (item.status, item.customer_input) == ("QUEUED", NEW)
        assert not Refund.objects.filter(order_item=item).exists()

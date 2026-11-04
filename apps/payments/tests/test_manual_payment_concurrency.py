from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Plan
from apps.orders import services as orders
from apps.orders.models import DiscountCode, Order, OrderItemEvent
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock
from apps.payments import services
from apps.payments.models import Payment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("first", ["confirm", "cancel", "expire"])
def test_confirmation_serializes_with_second_confirmation_and_release(
    buyer, plan, monkeypatch, first
):
    operator = User.objects.create_superuser("operator@example.test")
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=100, max_uses=1)
    order = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 1)], discount_code="SAVE")
    if first == "expire":
        Order.objects.filter(pk=order.pk).update(unpaid_expires_at=timezone.now())
    entered, release, pids = Event(), Event(), Queue()
    original = services.mark_order_paid if first == "confirm" else orders._release

    def held(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(
        services if first == "confirm" else orders,
        "mark_order_paid" if first == "confirm" else "_release",
        held,
    )

    def confirm():
        return services.record_manual_payment(
            order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="ref"
        )

    leading = (
        confirm
        if first == "confirm"
        else (
            (lambda: orders.expire_unpaid_orders())
            if first == "expire"
            else (lambda: orders.cancel_unpaid_order(order_id=order.pk, actor=buyer))
        )
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(in_connection, pids, leading)
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        contender = pool.submit(in_connection, pids, confirm)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        first_result, second_result = leader.result(timeout=10), contender.result(timeout=10)
    if first == "confirm":
        assert first_result.pk == second_result.pk
        assert Payment.objects.count() == 1 and order.items.get().status == "QUEUED"
        assert Plan.objects.get(pk=plan.pk).stock_quantity == 0
        assert DiscountCode.objects.get(code="SAVE").used_count == 1
    else:
        assert second_result is None and not Payment.objects.exists()
        assert order.items.get().status == "CANCELLED"
        assert Plan.objects.get(pk=plan.pk).stock_quantity == 1
        assert DiscountCode.objects.get(code="SAVE").used_count == 0
    assert OrderItemEvent.objects.filter(order_item__order=order).count() == 2


def test_expiry_waits_for_confirmed_payment_then_leaves_claims_alone(buyer, plan, monkeypatch):
    operator = User.objects.create_superuser("operator@example.test")
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    order = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 1)])
    entered, release, pids = Event(), Event(), Queue()
    original = services.mark_order_paid

    def held(**kwargs):
        original(**kwargs)
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(services, "mark_order_paid", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            in_connection,
            pids,
            lambda: services.record_manual_payment(
                order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="ref"
            ),
        )
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        monkeypatch.setattr(timezone, "now", lambda: order.unpaid_expires_at + timedelta(seconds=1))
        expiry = pool.submit(in_connection, pids, orders.expire_unpaid_orders)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert first.result(timeout=10).status == "verified"
        assert expiry.result(timeout=10) == 0
    assert order.items.get().status == "QUEUED" and Plan.objects.get(pk=plan.pk).stock_quantity == 0

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Plan
from apps.orders import services as orders
from apps.orders.models import DiscountCode, Order, OrderItem, OrderItemEvent
from apps.orders.tests.test_lifecycle_concurrency import in_connection, separate_plan, wait_for_lock
from apps.payments import services

pytestmark = pytest.mark.django_db(transaction=True)


def buy(user, quote, key):
    return services.checkout(
        user=user,
        quote=quote.token,
        checkout_id=key,
        customer_inputs={},
    )


@pytest.mark.parametrize("identical", [True, False])
def test_overlapping_tabs_serialize_before_reading_or_consuming_cart(
    buyer, plan, monkeypatch, identical
):
    cart = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=cart, plan=plan)
    quote = services.quote_checkout(user=buyer)
    first_key = uuid.uuid4()
    second_key = first_key if identical else uuid.uuid4()
    entered, release, pids = Event(), Event(), Queue()
    original = orders._price

    def hold(**kwargs):
        entered.set()
        assert release.wait(10)
        return original(**kwargs)

    monkeypatch.setattr(orders, "_price", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, lambda: buy(buyer, quote, first_key))
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, lambda: buy(buyer, quote, second_key))
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        order = first.result(timeout=10)
        replay = second.result(timeout=10)
    assert order is not None
    assert replay.pk == order.pk if identical else replay is None
    assert Order.objects.count() == 1 and not cart.items.exists()


@pytest.mark.parametrize("resource", ["stock", "coupon"])
def test_competing_customers_preserve_losing_cart_and_claim_only_once(
    buyer, plan, monkeypatch, resource
):
    other = User.objects.create_user("other@example.test")
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    chosen = separate_plan(plan) if resource == "coupon" else plan
    carts = [Cart.objects.create(user=user) for user in (buyer, other)]
    for cart, selected in zip(carts, (plan, chosen), strict=True):
        CartItem.objects.create(cart=cart, plan=selected)
    coupon = DiscountCode.objects.create(code="LAST", kind="fixed", value=100, max_uses=1)
    code = coupon.code if resource == "coupon" else ""
    quotes = [services.quote_checkout(user=user, discount_code=code) for user in (buyer, other)]
    entered, release, pids = Event(), Event(), Queue()
    original = orders._price

    def hold(**kwargs):
        if kwargs["user_id"] == buyer.pk:
            entered.set()
            assert release.wait(10)
        return original(**kwargs)

    monkeypatch.setattr(orders, "_price", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, lambda: buy(buyer, quotes[0], uuid.uuid4()))
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, lambda: buy(other, quotes[1], uuid.uuid4()))
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert first.result(timeout=10) is not None
        assert second.result(timeout=10) is None
    assert not carts[0].items.exists() and carts[1].items.get().quantity == 1
    assert Order.objects.count() == 1
    plan.refresh_from_db()
    assert plan.stock_quantity == 0
    coupon.refresh_from_db()
    assert coupon.used_count == (1 if resource == "coupon" else 0)


def test_review_releases_each_legacy_order_transaction_before_acquiring_the_next_product(
    buyer, plan, monkeypatch
):
    other = User.objects.create_user("other@example.test")
    second_plan = separate_plan(plan)
    Plan.objects.filter(pk__in=[plan.pk, second_plan.pk]).update(stock_quantity=1)
    due = timezone.now() - timedelta(seconds=1)
    legacy = []
    for selected in (second_plan, plan):
        order = Order.objects.create(
            user=buyer,
            subtotal=200000,
            total_amount=200000,
            created_at=due - timedelta(hours=24),
            unpaid_expires_at=due,
        )
        item = OrderItem.objects.create(
            order=order,
            plan=selected,
            price_snapshot=200000,
            cost_snapshot=100000,
            product_snapshot={"name": "محصول قدیمی"},
            stock_debited=True,
        )
        OrderItemEvent.objects.create(
            order_item=item,
            from_status=None,
            to_status=item.status,
            actor=OrderItemEvent.Actor.CUSTOMER,
            actor_user=buyer,
        )
        legacy.append(order)
    cart = Cart.objects.create(user=other)
    for selected in (plan, second_plan):
        CartItem.objects.create(cart=cart, plan=selected)
    quote = services.quote_checkout(user=other)
    entered, release, pids = Event(), Event(), Queue()
    original = orders._release

    def hold(order, *args, **kwargs):
        result = original(order, *args, **kwargs)
        if order.pk == legacy[0].pk:
            entered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(orders, "_release", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, lambda: services.prepare_checkout(user=buyer))
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, lambda: buy(other, quote, uuid.uuid4()))
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert first.result(timeout=15) is None
        assert second.result(timeout=15) is not None
    assert (
        OrderItem.objects.filter(order__in=legacy, status=OrderItem.Status.CANCELLED).count() == 2
    )
    assert not cart.items.exists()
    for selected in (plan, second_plan):
        selected.refresh_from_db()
        assert selected.stock_quantity == 1

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Barrier, Event, local
from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models.query import QuerySet
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.orders import services
from apps.orders.models import DiscountCode, DiscountRedemption, Order, OrderItemEvent
from apps.orders.services import OrderLine

pytestmark = pytest.mark.django_db(transaction=True)


def separate_plan(plan):
    product = Product.objects.create(
        category=plan.product.category,
        name="محصول دوم",
        slug="other",
        status=Product.Status.ACTIVE,
    )
    return Plan.objects.create(
        product=product,
        title="یک‌ماهه",
        sale_price=200000,
        cost_price=100000,
        stock_quantity=1,
        input_type=Plan.InputType.NONE,
        telegram_required=False,
    )


def wait_for_lock(waiter, holder):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_blocking_pids(%s)", [waiter])
            if holder in cursor.fetchone()[0]:
                return
        time.sleep(0.01)
    pytest.fail("The competing operation did not wait for the expected PostgreSQL lock.")


def in_connection(pids, operation):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pids.put(cursor.fetchone()[0])
            cursor.execute("SET statement_timeout = '12s'")
        try:
            return operation()
        except ValidationError:
            return None
    finally:
        connection.close()


@pytest.mark.parametrize("resource", ["stock", "coupon"])
def test_last_resource_is_rechecked_after_the_competing_transaction_commits(
    buyer, plan, monkeypatch, resource
):
    other = User.objects.create_user("other@example.test")
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    second_plan = separate_plan(plan) if resource == "coupon" else plan
    code = DiscountCode.objects.create(code="LAST", kind="fixed", value=100, max_uses=1)
    entered, release = Event(), Event()
    pids = Queue()
    original = services._price

    def held_price(**kwargs):
        if kwargs["user_id"] == buyer.pk:
            entered.set()
            assert release.wait(10)
        return original(**kwargs)

    monkeypatch.setattr(services, "_price", held_price)

    def buy(user, chosen):
        return services.place_order(
            user=user,
            lines=[OrderLine(chosen, 1)],
            activation_consent=True,
            discount_code=code.code if resource == "coupon" else "",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, lambda: buy(buyer, plan))
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, lambda: buy(other, second_plan))
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert first.result(timeout=10) is not None
        assert second.result(timeout=10) is None
    assert Order.objects.count() == 1
    plan.refresh_from_db()
    code.refresh_from_db()
    assert plan.stock_quantity == 0
    assert code.used_count == (1 if resource == "coupon" else 0)
    if resource == "coupon":
        second_plan.refresh_from_db()
        assert second_plan.stock_quantity == 1


def test_cancellation_racing_expiry_releases_the_order_only_once(buyer, plan, monkeypatch):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=2)
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=100, max_uses=1)
    order = services.place_order(
        user=buyer, lines=[OrderLine(plan, 2)], activation_consent=True, discount_code=code.code
    )
    Order.objects.filter(pk=order.pk).update(unpaid_expires_at=timezone.now())
    entered, release = Event(), Event()
    pids = Queue()
    original = services._release

    def held_release(*args, **kwargs):
        if kwargs.get("actor") is not None:
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(services, "_release", held_release)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            in_connection,
            pids,
            lambda: services.cancel_unpaid_order(order_id=order.pk, actor=buyer),
        )
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, services.expire_unpaid_orders)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert first.result(timeout=10).pk == order.pk
        assert second.result(timeout=10) == 0
    plan.refresh_from_db()
    code.refresh_from_db()
    assert plan.stock_quantity == 2 and code.used_count == 0
    assert OrderItemEvent.objects.count() == 4
    assert DiscountRedemption.objects.get(order=order).released_at is not None


def test_a_checkout_identifier_cannot_be_claimed_by_two_customers(buyer, plan, monkeypatch):
    other = User.objects.create_user("other@example.test")
    other_plan = separate_plan(plan)
    checkout_id = uuid.uuid4()
    barrier = Barrier(2)
    original = services._price

    def simultaneous_price(**kwargs):
        value = original(**kwargs)
        barrier.wait(timeout=10)
        return value

    monkeypatch.setattr(services, "_price", simultaneous_price)
    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(
                in_connection,
                pids,
                lambda user=user, chosen=chosen: services.place_order(
                    user=user,
                    lines=[OrderLine(chosen, 1)],
                    activation_consent=True,
                    checkout_id=checkout_id,
                    submission_digest="a" * 64,
                ),
            )
            for user, chosen in [(buyer, plan), (other, other_plan)]
        ]
        orders = [result.result(timeout=15) for result in results]
    assert sum(order is not None for order in orders) == 1
    assert Order.objects.count() == 1 and OrderItemEvent.objects.count() == 1


@pytest.mark.parametrize("editor", ["product", "discount", "delete_single", "delete_bulk"])
def test_admin_and_checkout_use_the_same_lock_order(buyer, plan, monkeypatch, editor):
    operator = User.objects.create_superuser("operator@example.test")
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=100)
    entered, release = Event(), Event()
    pids = Queue()
    state = local()
    original = QuerySet._fetch_all

    def hold_product(queryset):
        original(queryset)
        if (
            getattr(state, "checkout", False)
            and not entered.is_set()
            and queryset.model is Product
            and queryset.query.select_for_update
        ):
            entered.set()
            assert release.wait(10)

    monkeypatch.setattr(QuerySet, "_fetch_all", hold_product)

    def checkout():
        state.checkout = True
        return services.place_order(
            user=buyer, lines=[OrderLine(plan, 1)], activation_consent=True, discount_code=code.code
        )

    def edit():
        if editor.startswith("delete_"):
            client = Client()
            client.force_login(operator)
            url = (
                reverse("admin:catalog_product_delete", args=[plan.product_id])
                if editor == "delete_single"
                else reverse("admin:catalog_product_changelist")
            )
            data = {"post": "yes"}
            if editor == "delete_bulk":
                data.update(action="delete_selected", _selected_action=[str(plan.product_id)])
            return client.post(url, data).status_code == 200
        request = RequestFactory().post("/")
        request.user = operator
        with transaction.atomic():
            product = Product.objects.get(pk=plan.product_id)
            if editor == "product":
                admin.site._registry[Product].get_form(request, product, change=True)
                product.name = "نام جدید"
                product.save()
            else:
                edited_code = DiscountCode.objects.get(pk=code.pk)
                edited_code.value = 200
                form = SimpleNamespace(
                    cleaned_data={"products": Product.objects.filter(pk=product.pk)}
                )
                admin.site._registry[DiscountCode].save_model(request, edited_code, form, True)
                edited_code.products.add(product)
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, checkout)
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, edit)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert first.result(timeout=15) is not None
        assert second.result(timeout=15) is True
    assert Product.objects.filter(pk=plan.product_id).exists()
    assert Plan.objects.filter(pk=plan.pk).exists()


@pytest.mark.parametrize("bulk", [False, True])
def test_product_deletion_without_orders_still_removes_its_plans(plan, bulk):
    client = Client()
    client.force_login(User.objects.create_superuser("operator@example.test"))
    url = (
        reverse("admin:catalog_product_changelist")
        if bulk
        else reverse("admin:catalog_product_delete", args=[plan.product_id])
    )
    data = {"post": "yes"}
    if bulk:
        data.update(action="delete_selected", _selected_action=[str(plan.product_id)])
    assert client.post(url, data).status_code == 302
    assert not Product.objects.filter(pk=plan.product_id).exists()
    assert not Plan.objects.filter(pk=plan.pk).exists()


def test_expiring_old_orders_and_buying_opposite_plans_lock_the_combined_resources(
    buyer, plan, monkeypatch
):
    other = User.objects.create_user("other@example.test")
    second_plan = separate_plan(plan)
    Plan.objects.update(stock_quantity=None)
    for user, chosen in [(buyer, plan), (other, second_plan)]:
        order = services.place_order(
            user=user, lines=[OrderLine(chosen, 1)], activation_consent=True
        )
        Order.objects.filter(pk=order.pk).update(unpaid_expires_at=timezone.now())
    barrier = Barrier(2)
    original = services._plans

    def together(ids, *, lock):
        barrier.wait(timeout=10)
        return original(ids, lock=lock)

    monkeypatch.setattr(services, "_plans", together)
    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(
                in_connection,
                pids,
                lambda user=user, chosen=chosen: services.place_order(
                    user=user, lines=[OrderLine(chosen, 1)], activation_consent=True
                ),
            )
            for user, chosen in [(buyer, second_plan), (other, plan)]
        ]
        assert all(result.result(timeout=15) is not None for result in results)
    assert Order.objects.count() == 4
    assert Order.objects.filter(cancelled_at__isnull=False).count() == 2

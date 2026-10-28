from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connections, transaction
from django.db.models import Sum

from apps.cart.models import CartItem
from apps.cart.services import (
    add_to_cart,
    get_cart,
    merge_carts,
    set_quantity,
)
from apps.cart.tests.test_services import request_for
from apps.catalog.models import Plan

pytestmark = pytest.mark.django_db


def limited(plan, quantity):
    plan.stock_quantity = quantity
    plan.save()
    return plan


def test_stock_cannot_be_negative(plan):
    with pytest.raises(IntegrityError), transaction.atomic():
        Plan.objects.filter(pk=plan.pk).update(stock_quantity=-1)


def test_failure_after_adding_rolls_back_cart_writes(plan):
    from unittest.mock import patch

    limited(plan, 1)
    with patch("apps.cart.services._touch", side_effect=RuntimeError("failed session save")):
        with pytest.raises(RuntimeError):
            add_to_cart(request_for(), plan_id=plan.pk)
    assert not CartItem.objects.exists()
    add_to_cart(request_for(), plan_id=plan.pk)


def test_merge_quantity_cap_does_not_consume_stock(plan, buyer):
    limited(plan, 4)
    guest, account = request_for(), request_for(buyer)
    add_to_cart(guest, plan_id=plan.pk, quantity=2)
    add_to_cart(account, plan_id=plan.pk, quantity=2)
    assert merge_carts(user=buyer, guest_cart_id=get_cart(guest).pk)
    assert get_cart(account).items.get().quantity == 3
    add_to_cart(request_for(), plan_id=plan.pk)


@pytest.mark.django_db(transaction=True)
def test_same_user_merge_and_another_cart_keep_independent_selections(plan, buyer):
    limited(plan, 3)
    guest, account = request_for(), request_for(buyer)
    add_to_cart(guest, plan_id=plan.pk)
    add_to_cart(account, plan_id=plan.pk)
    guest_id = get_cart(guest).pk

    def operation(merge):
        close_old_connections()
        try:
            if merge:
                merge_carts(user=buyer, guest_cart_id=guest_id)
            else:
                add_to_cart(request_for(), plan_id=plan.pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(operation, [True, False]))
    assert CartItem.objects.aggregate(total=Sum("quantity"))["total"] == 3
    assert get_cart(account).items.get().quantity == 2


@pytest.mark.django_db(transaction=True)
def test_competing_customers_can_both_save_the_last_unit_without_claiming_it(plan):
    limited(plan, 1)

    def reserve():
        close_old_connections()
        try:
            add_to_cart(request_for(), plan_id=plan.pk)
            return True
        except ValidationError:
            return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert results == [True, True]
    assert CartItem.objects.count() == 2
    plan.refresh_from_db()
    assert plan.stock_quantity == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["quantity", "merge"])
def test_admin_stock_edit_and_own_cart_edit_do_not_deadlock(plan, buyer, operation):
    from threading import Event
    from unittest.mock import patch

    from django.contrib.admin.models import CHANGE, LogEntry
    from django.contrib.contenttypes.models import ContentType
    from django.db.models.query import QuerySet

    from apps.accounts.models import User

    limited(plan, 3)
    add_to_cart(request_for(buyer), plan_id=plan.pk)
    guest_id = None
    if operation == "merge":
        guest = request_for()
        add_to_cart(guest, plan_id=plan.pk)
        guest_id = get_cart(guest).pk
    content_type = ContentType.objects.get_for_model(Plan).pk
    cart_locked, stock_locked = Event(), Event()
    original_first = QuerySet.first

    def pause_after_owner_lock(queryset):
        result = original_first(queryset)
        if queryset.model is User and queryset.query.select_for_update:
            cart_locked.set()
            assert stock_locked.wait(5)
        return result

    def edit_cart():
        close_old_connections()
        try:
            if operation == "merge":
                merge_carts(user=buyer, guest_cart_id=guest_id)
            else:
                set_quantity(request_for(buyer), plan_id=plan.pk, quantity=2)
        finally:
            connections.close_all()

    def edit_stock():
        close_old_connections()
        try:
            assert cart_locked.wait(5)
            with transaction.atomic():
                stock = Plan.objects.select_for_update().get(pk=plan.pk)
                stock_locked.set()
                stock.stock_quantity = 4
                stock.save()
                LogEntry.objects.create(
                    user_id=buyer.pk,
                    content_type_id=content_type,
                    object_id=str(plan.pk),
                    object_repr="stock edit",
                    action_flag=CHANGE,
                    change_message="Stock changed",
                )
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        finally:
            connections.close_all()

    with patch.object(QuerySet, "first", pause_after_owner_lock):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(edit_cart), pool.submit(edit_stock)]
            for future in futures:
                future.result(timeout=10)
    assert get_cart(request_for(buyer)).items.get().quantity == 2
    assert LogEntry.objects.filter(user_id=buyer.pk).count() == 1

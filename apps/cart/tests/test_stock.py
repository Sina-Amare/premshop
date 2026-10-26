from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connections, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.cart.models import CartItem
from apps.cart.services import (
    add_to_cart,
    cart_summary,
    get_cart,
    merge_carts,
    remove_from_cart,
    set_quantity,
)
from apps.cart.tests.test_services import request_for
from apps.catalog.models import Plan

pytestmark = pytest.mark.django_db


def limited(plan, quantity):
    plan.stock_quantity = quantity
    plan.save()
    return plan


def test_two_carts_share_stock_and_reduction_and_removal_release_units(plan):
    limited(plan, 2)
    first, second = request_for(), request_for()
    add_to_cart(first, plan_id=plan.pk, quantity=2)
    with pytest.raises(ValidationError, match="موجود"):
        add_to_cart(second, plan_id=plan.pk)
    set_quantity(first, plan_id=plan.pk, quantity=1)
    add_to_cart(second, plan_id=plan.pk)
    assert CartItem.objects.aggregate(total=Sum("quantity"))["total"] == 2
    remove_from_cart(first, plan_id=plan.pk)
    set_quantity(second, plan_id=plan.pk, quantity=2)
    assert get_cart(second).items.get().quantity == 2
    plan.refresh_from_db()
    assert plan.stock_quantity == 2


def test_reservation_expires_after_twenty_minutes_without_removing_cart(plan):
    limited(plan, 1)
    first, second = request_for(), request_for()
    before = timezone.now()
    item = add_to_cart(first, plan_id=plan.pk)
    assert (
        before + timedelta(minutes=20)
        <= item.reserved_until
        <= timezone.now() + timedelta(minutes=20)
    )
    CartItem.objects.filter(pk=item.pk).update(reserved_until=before - timedelta(seconds=1))
    add_to_cart(second, plan_id=plan.pk)
    assert get_cart(first).items.get().quantity == 1
    assert cart_summary(get_cart(first)).total == 0
    with pytest.raises(ValidationError, match="موجود"):
        set_quantity(first, plan_id=plan.pk, quantity=1)
    remove_from_cart(second, plan_id=plan.pk)
    set_quantity(first, plan_id=plan.pk, quantity=1)
    assert cart_summary(get_cart(first)).total == plan.sale_price


def test_reads_and_quantity_edits_do_not_extend_a_live_reservation(plan):
    limited(plan, 3)
    request = request_for()
    item = add_to_cart(request, plan_id=plan.pk)
    deadline = item.reserved_until
    cart_summary(get_cart(request))
    set_quantity(request, plan_id=plan.pk, quantity=2)
    item.refresh_from_db()
    assert item.reserved_until == deadline
    set_quantity(request, plan_id=plan.pk, quantity=1)
    item.refresh_from_db()
    assert item.reserved_until == deadline


def test_merge_preserves_stock_and_the_earlier_live_deadline(plan, buyer):
    limited(plan, 3)
    guest, account = request_for(), request_for(buyer)
    guest_item = add_to_cart(guest, plan_id=plan.pk)
    account_item = add_to_cart(account, plan_id=plan.pk)
    earlier = min(guest_item.reserved_until, account_item.reserved_until)
    merge_carts(user=buyer, guest_cart_id=get_cart(guest).pk)
    merged = get_cart(account).items.get()
    assert merged.quantity == 2 and merged.reserved_until == earlier
    add_to_cart(request_for(), plan_id=plan.pk)
    with pytest.raises(ValidationError, match="موجود"):
        add_to_cart(request_for(), plan_id=plan.pk)


def test_expired_guest_merge_cannot_take_another_customers_reservation(plan, buyer):
    limited(plan, 1)
    guest, stranger = request_for(), request_for()
    item = add_to_cart(guest, plan_id=plan.pk)
    CartItem.objects.filter(pk=item.pk).update(reserved_until=timezone.now() - timedelta(seconds=1))
    add_to_cart(stranger, plan_id=plan.pk)
    merge_carts(user=buyer, guest_cart_id=get_cart(guest).pk)
    account_cart = get_cart(request_for(buyer))
    assert account_cart.items.get().quantity == 1
    assert cart_summary(account_cart).total == 0


@pytest.mark.parametrize("account_has_cart", [False, True])
def test_login_does_not_start_a_new_hold_for_expired_selections(plan, buyer, account_has_cart):
    limited(plan, 2)
    guest, account = request_for(), request_for(buyer)
    add_to_cart(guest, plan_id=plan.pk)
    if account_has_cart:
        add_to_cart(account, plan_id=plan.pk)
    CartItem.objects.update(reserved_until=timezone.now() - timedelta(seconds=1))
    merge_carts(user=buyer, guest_cart_id=get_cart(guest).pk)
    merged = get_cart(account).items.get()
    assert merged.quantity == (2 if account_has_cart else 1)
    assert merged.reserved_until is None
    assert cart_summary(get_cart(account)).total == 0
    add_to_cart(request_for(), plan_id=plan.pk, quantity=2)


@pytest.mark.parametrize("expired_owner", ["guest", "account"])
def test_mixed_expired_and_live_merge_keeps_the_live_deadline(plan, buyer, expired_owner):
    limited(plan, 2)
    guest, account = request_for(), request_for(buyer)
    guest_item = add_to_cart(guest, plan_id=plan.pk)
    account_item = add_to_cart(account, plan_id=plan.pk)
    expired = guest_item if expired_owner == "guest" else account_item
    live = account_item if expired_owner == "guest" else guest_item
    CartItem.objects.filter(pk=expired.pk).update(
        reserved_until=timezone.now() - timedelta(seconds=1)
    )
    merge_carts(user=buyer, guest_cart_id=get_cart(guest).pk)
    merged = get_cart(account).items.get()
    assert merged.quantity == 2 and merged.reserved_until == live.reserved_until
    with pytest.raises(ValidationError, match="موجود"):
        add_to_cart(request_for(), plan_id=plan.pk)


def test_stock_cannot_be_negative_or_lower_than_live_reservations(plan):
    limited(plan, 2)
    add_to_cart(request_for(), plan_id=plan.pk)
    plan.stock_quantity = 0
    with pytest.raises(ValidationError, match="رزرو"):
        plan.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        Plan.objects.filter(pk=plan.pk).update(stock_quantity=-1)


def test_stock_edit_form_rejects_lower_stock_and_reports_current_reserved_units(plan):
    from django.forms import modelform_factory

    from apps.catalog.admin import PlanForm, stock_levels

    limited(plan, 3)
    add_to_cart(request_for(), plan_id=plan.pk, quantity=2)
    form_class = modelform_factory(Plan, form=PlanForm, fields=["stock_quantity"])
    form = form_class({"stock_quantity": 1}, instance=plan)
    assert not form.is_valid()
    assert "رزروشده" in str(form.errors["stock_quantity"])
    plan.refresh_from_db()
    assert stock_levels(plan) == "۱ قابل خرید، ۲ رزرو شده"


def test_failure_after_reserving_rolls_back_all_stock_and_cart_writes(plan):
    from unittest.mock import patch

    limited(plan, 1)
    with patch("apps.cart.services._touch", side_effect=RuntimeError("failed session save")):
        with pytest.raises(RuntimeError):
            add_to_cart(request_for(), plan_id=plan.pk)
    assert not CartItem.objects.exists()
    add_to_cart(request_for(), plan_id=plan.pk)


def test_merge_quantity_cap_releases_excess_reservations(plan, buyer):
    limited(plan, 4)
    guest, account = request_for(), request_for(buyer)
    add_to_cart(guest, plan_id=plan.pk, quantity=2)
    add_to_cart(account, plan_id=plan.pk, quantity=2)
    assert merge_carts(user=buyer, guest_cart_id=get_cart(guest).pk)
    assert get_cart(account).items.get().quantity == 3
    add_to_cart(request_for(), plan_id=plan.pk)


@pytest.mark.django_db(transaction=True)
def test_same_user_merge_competing_with_another_buyer_does_not_oversell(plan, buyer):
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
def test_competing_customers_cannot_both_reserve_the_last_unit(plan):
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
    assert sorted(results) == [False, True]
    assert CartItem.objects.count() == 1

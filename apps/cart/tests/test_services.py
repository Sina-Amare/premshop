from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from apps.cart.services import (
    GUEST_CART_ID,
    add_to_cart,
    cart_count,
    cart_summary,
    get_cart,
    merge_carts,
    remove_from_cart,
    set_quantity,
)

pytestmark = pytest.mark.django_db


def request_for(user=None, session=None):
    request = RequestFactory().get("/cart/")
    request.user = user or AnonymousUser()
    request.session = session if session is not None else SessionStore()
    return request


def test_reading_empty_cart_does_not_create_anything():
    request = request_for()
    assert get_cart(request) is None
    assert cart_count(request) == 0
    assert cart_summary(None).total == 0
    assert not Cart.objects.exists()
    assert request.session.session_key is None


def test_cart_contains_only_identity_plan_quantity_and_timestamps(plan):
    request = request_for()
    add_to_cart(request, plan_id=plan.pk, quantity=2)
    assert {f.name for f in Cart._meta.fields} == {
        "id",
        "user",
        "session_key",
        "created_at",
        "updated_at",
    }
    assert {f.name for f in CartItem._meta.fields} == {
        "id",
        "cart",
        "plan",
        "quantity",
        "added_at",
    }
    cart = get_cart(request)
    assert cart.session_key == request.session.session_key
    assert request.session[GUEST_CART_ID] == cart.pk
    assert request.session.get_expiry_age() == 30 * 86400


@pytest.mark.parametrize("quantity", [0, -1, 4, True, 1.5, "2", None])
def test_invalid_add_does_not_create_cart_or_session(plan, quantity):
    request = request_for()
    with pytest.raises(ValidationError):
        add_to_cart(request, plan_id=plan.pk, quantity=quantity)
    assert not Cart.objects.exists()
    assert request.session.session_key is None


def test_increment_limits_updates_removal_and_fresh_prices(plan):
    request = request_for()
    add_to_cart(request, plan_id=plan.pk)
    add_to_cart(request, plan_id=plan.pk)
    cart = get_cart(request)
    assert cart.items.get().quantity == 2
    with pytest.raises(ValidationError):
        add_to_cart(request, plan_id=plan.pk, quantity=2)
    assert cart.items.get().quantity == 2
    with pytest.raises(ValidationError):
        set_quantity(request, plan_id=plan.pk, quantity=4)
    assert cart.items.get().quantity == 2
    plan.sale_price = Decimal("250000")
    plan.promo_price = Decimal("180000")
    plan.save()
    summary = cart_summary(cart)
    assert summary.lines[0].unit_price == Decimal("180000")
    assert summary.total == Decimal("360000")
    set_quantity(request, plan_id=plan.pk, quantity=1)
    assert cart_count(request) == 1
    set_quantity(request, plan_id=plan.pk, quantity=0)
    assert not cart.items.exists()
    remove_from_cart(request, plan_id=plan.pk)


def test_input_plan_has_one_unit_and_changed_ceiling_is_flagged(plan):
    request = request_for()
    add_to_cart(request, plan_id=plan.pk, quantity=3)
    plan.max_quantity = 1
    plan.requires_customer_input = True
    plan.save()
    assert cart_summary(get_cart(request)).lines[0].quantity_invalid
    with pytest.raises(ValidationError):
        set_quantity(request, plan_id=plan.pk, quantity=2)
    set_quantity(request, plan_id=plan.pk, quantity=1)
    with pytest.raises(ValidationError):
        add_to_cart(request, plan_id=plan.pk)


@pytest.mark.parametrize("unavailable", ["plan", "draft", "missing"])
def test_unavailable_plan_cannot_be_added_or_updated(plan, unavailable):
    request = request_for()
    add_to_cart(request, plan_id=plan.pk)
    if unavailable == "plan":
        type(plan).objects.filter(pk=plan.pk).update(is_available=False)
    elif unavailable == "draft":
        type(plan.product).objects.filter(pk=plan.product_id).update(status="draft")
    else:
        plan.pk += 1000
    with pytest.raises(ValidationError):
        add_to_cart(request, plan_id=plan.pk)
    with pytest.raises(ValidationError):
        set_quantity(request, plan_id=plan.pk, quantity=2)
    assert get_cart(request).items.get().quantity == 1


def test_guest_reference_is_not_used_as_authorization(plan, buyer):
    original = request_for()
    add_to_cart(original, plan_id=plan.pk)
    stranger = request_for()
    stranger.session[GUEST_CART_ID] = get_cart(original).pk
    assert get_cart(stranger) is None
    remove_from_cart(stranger, plan_id=plan.pk)
    assert get_cart(original).items.get().quantity == 1
    account = request_for(buyer)
    remove_from_cart(account, plan_id=plan.pk)
    assert get_cart(original).items.exists()


def test_claim_preserves_rows_and_merge_is_atomic_idempotent_and_capped(plan, buyer):
    request = request_for()
    item = add_to_cart(request, plan_id=plan.pk, quantity=2)
    guest_id, added_at = item.cart_id, item.added_at
    merge_carts(user=buyer, guest_cart_id=guest_id)
    item.refresh_from_db()
    assert item.cart_id == guest_id and item.added_at == added_at
    assert Cart.objects.get(pk=guest_id).user_id == buyer.pk
    assert Cart.objects.get(pk=guest_id).session_key is None
    another = request_for()
    second = add_to_cart(another, plan_id=plan.pk, quantity=2)
    with patch.object(Cart, "delete", side_effect=RuntimeError), pytest.raises(RuntimeError):
        merge_carts(user=buyer, guest_cart_id=second.cart_id)
    assert CartItem.objects.get(pk=item.pk).quantity == 2
    assert Cart.objects.filter(pk=second.cart_id).exists()
    assert merge_carts(user=buyer, guest_cart_id=second.cart_id) is True
    merge_carts(user=buyer, guest_cart_id=second.cart_id)
    assert CartItem.objects.get(pk=item.pk).quantity == 3
    assert Cart.objects.count() == 1


def test_claim_applies_current_ceiling(plan, buyer):
    request = request_for()
    item = add_to_cart(request, plan_id=plan.pk, quantity=3)
    type(plan).objects.filter(pk=plan.pk).update(max_quantity=1)
    assert merge_carts(user=buyer, guest_cart_id=item.cart_id) is True
    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.parametrize("values", [{}, {"user_id": 999, "session_key": "both"}])
def test_database_rejects_ambiguous_owner(values, buyer):
    if "user_id" in values:
        values["user_id"] = buyer.pk
    with pytest.raises(IntegrityError), transaction.atomic():
        Cart.objects.create(**values)


def test_database_rejects_duplicate_owners_lines_and_invalid_quantities(plan, buyer):
    cart = Cart.objects.create(user=buyer)
    item = CartItem.objects.create(cart=cart, plan=plan)
    for operation in (
        lambda: Cart.objects.create(user=buyer),
        lambda: CartItem.objects.create(cart=cart, plan=plan),
        lambda: CartItem.objects.filter(pk=item.pk).update(quantity=0),
        lambda: CartItem.objects.filter(pk=item.pk).update(quantity=11),
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            operation()
    Cart.objects.create(session_key="unique")
    with pytest.raises(IntegrityError), transaction.atomic():
        Cart.objects.create(session_key="unique")


def test_purge_only_old_guests_and_can_repeat(plan, buyer):
    from django.core.management import call_command

    old = Cart.objects.create(session_key="old")
    CartItem.objects.create(cart=old, plan=plan)
    account = Cart.objects.create(user=buyer)
    fresh = Cart.objects.create(session_key="fresh")
    Cart.objects.filter(pk__in=[old.pk, account.pk]).update(
        updated_at=timezone.now() - timedelta(days=31)
    )
    call_command("purge_guest_carts")
    call_command("purge_guest_carts")
    assert set(Cart.objects.values_list("pk", flat=True)) == {account.pk, fresh.pk}


@pytest.mark.django_db(transaction=True)
def test_concurrent_adds_and_merges_do_not_lose_units(plan, buyer):
    def add(_):
        close_old_connections()
        try:
            add_to_cart(request_for(buyer), plan_id=plan.pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(add, range(2)))
    assert CartItem.objects.get().quantity == 2
    guest = request_for()
    item = add_to_cart(guest, plan_id=plan.pk)

    def merge(_):
        close_old_connections()
        try:
            merge_carts(user=buyer, guest_cart_id=item.cart_id)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(merge, range(2)))
    assert CartItem.objects.get().quantity == 3
    assert Cart.objects.count() == 1


def test_account_cart_is_shared_by_two_sessions(plan, buyer):
    first, second = Client(), Client()
    first.force_login(buyer)
    second.force_login(buyer)
    first.post("/cart/add/", {"plan": plan.pk, "quantity": 2})
    assert second.get("/cart/").context["cart"].lines[0].quantity == 2


def test_already_started_guest_edit_cannot_recreate_a_cart_after_login(client, plan, buyer):
    client.post("/cart/add/", {"plan": plan.pk})
    stale = request_for(session=SessionStore(session_key=client.session.session_key))
    assert stale.session[GUEST_CART_ID] == Cart.objects.get().pk
    client.post("/login/", {"email": buyer.email, "password": "CartPassword!2026"})
    with pytest.raises(ValidationError):
        add_to_cart(stale, plan_id=plan.pk)
    assert Cart.objects.count() == 1
    assert CartItem.objects.get(cart__user=buyer).quantity == 1


def test_first_add_is_merged_even_if_login_already_cached_the_session(client, plan, buyer):
    from apps.accounts.models import User

    key = client.session.session_key
    guest = request_for(session=SessionStore(session_key=key))
    real_check = User.check_password

    def check_with_concurrent_add(user, password):
        valid = real_check(user, password)
        if valid:
            add_to_cart(guest, plan_id=plan.pk)
        return valid

    with patch.object(User, "check_password", check_with_concurrent_add):
        response = client.post("/login/", {"email": buyer.email, "password": "CartPassword!2026"})
    assert response.status_code == 302
    assert Cart.objects.count() == 1
    assert CartItem.objects.get(cart__user=buyer).quantity == 1


def test_changing_a_line_that_is_gone_says_so_instead_of_reporting_success(plan):
    """A stale tab pressed «+» on a line another tab had removed and was told
    the quantity had been updated."""
    request = request_for()
    with pytest.raises(ValidationError, match="دیگر در سبد خرید نیست"):
        set_quantity(request, plan_id=plan.pk, quantity=2)
    add_to_cart(request, plan_id=plan.pk)
    remove_from_cart(request, plan_id=plan.pk)
    with pytest.raises(ValidationError, match="دیگر در سبد خرید نیست"):
        set_quantity(request, plan_id=plan.pk, quantity=2)

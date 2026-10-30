import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
import time_machine
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.query import QuerySet

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Plan, Product
from apps.orders import services as orders
from apps.orders.models import DiscountCode, DiscountRedemption, Order, OrderItem, OrderItemEvent
from apps.orders.services import UnpaidOrderExists, cancel_unpaid_order
from apps.payments import services

pytestmark = pytest.mark.django_db


@pytest.fixture
def cart(buyer, plan):
    cart = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=cart, plan=plan, quantity=1)
    return cart


def submit(buyer, accepted, **changes):
    data = {
        "user": buyer,
        "quote": accepted.token,
        "checkout_id": uuid.uuid4(),
        "customer_inputs": {},
    }
    data.update(changes)
    return services.checkout(**data)


def test_checkout_commits_one_purchase_and_identical_retry_keeps_deadline(buyer, plan, cart):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=2)
    quote = services.quote_checkout(user=buyer)
    key = uuid.uuid4()
    order = submit(buyer, quote, checkout_id=key, phone="۰۹۱۲۳۴۵۶۷۸۹")
    assert order.total_amount == 200000 and not cart.items.exists()
    assert order.consent_accepted_at is None
    assert order.unpaid_expires_at == order.created_at + timedelta(hours=24)
    assert order.items.get().stock_debited is True
    buyer.refresh_from_db()
    assert buyer.phone == "09123456789"
    with time_machine.travel(order.created_at + timedelta(days=2), tick=False):
        replay = submit(buyer, quote, checkout_id=key, phone="09123456789")
    assert replay.pk == order.pk and replay.unpaid_expires_at == order.unpaid_expires_at
    assert Order.objects.count() == 1
    plan.refresh_from_db()
    assert plan.stock_quantity == 1


def test_key_reuse_changed_content_and_other_owner_are_refused(buyer, cart):
    quote = services.quote_checkout(user=buyer)
    key = uuid.uuid4()
    order = submit(buyer, quote, checkout_id=key)
    with pytest.raises(ValidationError):
        submit(buyer, quote, checkout_id=key, phone="09123456789")
    other = User.objects.create_user("other@example.test")
    with pytest.raises(ValidationError):
        submit(other, quote, checkout_id=key)
    assert Order.objects.get(pk=order.pk).user == buyer


@pytest.mark.parametrize("version", [1, 999])
def test_quotes_from_another_contract_version_cannot_create_orders(buyer, plan, cart, version):
    quote = services.quote_checkout(user=buyer)
    payload = signing.loads(quote.token, salt=services.QUOTE_SALT)
    assert payload["version"] == 2
    payload["version"] = version
    old = services.CheckoutQuote(
        quote.purchase, signing.dumps(payload, salt=services.QUOTE_SALT, compress=True)
    )
    with pytest.raises(ValidationError, match="خرید معتبر نیست"):
        submit(buyer, old)
    assert cart.items.exists() and not Order.objects.exists()


def test_purchase_digest_contains_product_terms_without_consent(buyer, cart):
    terms = services.quote_checkout(user=buyer).purchase.terms()
    assert "consent_text" not in terms and "consent_version" not in terms
    assert "refund_note" in terms["lines"][0]["facts"]


def test_two_tabs_cannot_consume_one_cart_twice_even_after_cancellation(buyer, cart):
    first, second = services.quote_checkout(user=buyer), services.quote_checkout(user=buyer)
    order = submit(buyer, first)
    with pytest.raises(UnpaidOrderExists) as error:
        submit(buyer, second)
    assert error.value.order.pk == order.pk
    cancel_unpaid_order(order_id=order.pk, actor=buyer)
    with pytest.raises(ValidationError):
        submit(buyer, second)
    assert Order.objects.count() == 1


@pytest.mark.parametrize("mutation", ["price", "promotion", "terms", "telegram", "cart", "replace"])
def test_changed_quote_never_consumes_anything(buyer, plan, cart, mutation):
    quote = services.quote_checkout(user=buyer)
    if mutation == "price":
        Plan.objects.filter(pk=plan.pk).update(sale_price=250000)
    elif mutation == "promotion":
        Plan.objects.filter(pk=plan.pk).update(promo_price=150000)
    elif mutation == "terms":
        Product.objects.filter(pk=plan.product_id).update(refund_note="شرایط ویرایش‌شده")
    elif mutation == "telegram":
        Plan.objects.filter(pk=plan.pk).update(telegram_required=True)
    elif mutation == "cart":
        CartItem.objects.filter(cart=cart).update(quantity=2)
    else:
        cart.items.all().delete()
        CartItem.objects.create(cart=cart, plan=plan, quantity=1)
    with pytest.raises(ValidationError) as error:
        submit(buyer, quote)
    assert error.value.code == "quote_changed"
    assert cart.items.exists() and not Order.objects.exists()


def test_expired_or_tampered_quote_and_inactive_account_are_refused(buyer, cart):
    with time_machine.travel("2026-10-29 10:00:00+00:00", tick=False):
        quote = services.quote_checkout(user=buyer)
    with time_machine.travel("2026-10-29 10:15:01+00:00", tick=False):
        with pytest.raises(ValidationError, match="مهلت"):
            submit(buyer, quote)
    with pytest.raises(ValidationError):
        submit(buyer, quote, quote=quote.token + "x")
    User.objects.filter(pk=buyer.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        services.quote_checkout(user=buyer)
    assert cart.items.exists() and not Order.objects.exists()


def test_quote_expiring_while_waiting_for_purchase_locks_rolls_back_every_claim(
    buyer, plan, cart, monkeypatch
):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=1000, max_uses=1)
    original = orders._price
    with time_machine.travel("2026-10-29 10:00:00+00:00", tick=False) as clock:
        quote = services.quote_checkout(user=buyer, discount_code="SAVE")

        def delayed(**kwargs):
            clock.shift(timedelta(minutes=16))
            return original(**kwargs)

        monkeypatch.setattr(orders, "_price", delayed)
        with pytest.raises(ValidationError, match="مهلت"):
            submit(buyer, quote)
    assert cart.items.exists() and not Order.objects.exists()
    plan.refresh_from_db()
    code.refresh_from_db()
    assert plan.stock_quantity == 1 and code.used_count == 0


@pytest.mark.parametrize("target", ["OrderItem", "OrderItemEvent", "DiscountRedemption"])
def test_write_failure_preserves_cart_stock_coupon_and_history(buyer, plan, cart, target):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=2)
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=1000)
    quote = services.quote_checkout(user=buyer, discount_code="save")
    with patch(f"apps.orders.services.{target}.objects.create", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            submit(buyer, quote)
    plan.refresh_from_db()
    code.refresh_from_db()
    assert plan.stock_quantity == 2 and code.used_count == 0
    assert cart.items.get().quantity == 1
    assert not any(
        model.objects.exists() for model in (Order, OrderItem, OrderItemEvent, DiscountRedemption)
    )


def test_cart_consumption_failure_rolls_back_the_purchase(buyer, plan, cart, monkeypatch):
    quote = services.quote_checkout(user=buyer)
    original = QuerySet.delete

    def fail_cart(queryset):
        if queryset.model is CartItem:
            raise RuntimeError("cart write failed")
        return original(queryset)

    monkeypatch.setattr(QuerySet, "delete", fail_cart)
    with pytest.raises(RuntimeError):
        submit(buyer, quote)
    assert cart.items.exists() and not Order.objects.exists()


def test_quote_binds_discount_and_input_but_contains_no_customer_values(buyer, plan, cart):
    Plan.objects.filter(pk=plan.pk).update(
        input_type="email", requires_customer_input=True, max_quantity=1, telegram_required=True
    )
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=12345)
    quote = services.quote_checkout(user=buyer, discount_code=" save ")
    order = submit(buyer, quote, customer_inputs={str(plan.pk): "Target@EXAMPLE.test"})
    assert order.total_amount == 187655 and order.discount_amount == 12345
    assert order.items.get().customer_input == "Target@example.test"
    payload = signing.loads(quote.token, salt="payments.quote")
    assert "example.test" not in str(payload) and buyer.email not in str(payload)
    assert "Target" not in order.submission_digest and len(order.submission_digest) == 64
    Plan.objects.filter(pk=plan.pk).update(
        input_type="none", requires_customer_input=False, telegram_required=False
    )
    assert (
        submit(
            buyer,
            quote,
            checkout_id=order.checkout_id,
            customer_inputs={str(plan.pk): "Target@example.test"},
        ).pk
        == order.pk
    )


def test_successful_retry_with_signing_key_rotation(buyer, cart, settings):
    quote = services.quote_checkout(user=buyer)
    order = submit(buyer, quote)
    settings.SECRET_KEY_FALLBACKS = [settings.SECRET_KEY]
    settings.SECRET_KEY = uuid.uuid4().hex
    assert submit(buyer, quote, checkout_id=order.checkout_id).pk == order.pk


def test_review_post_expires_overdue_claim_before_quoting_same_stock_and_coupon(buyer, plan, cart):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=1000, max_uses=1)
    quote = services.quote_checkout(user=buyer, discount_code="SAVE")
    old = submit(buyer, quote)
    CartItem.objects.create(cart=cart, plan=plan, quantity=1)
    with time_machine.travel(old.unpaid_expires_at, tick=False):
        services.prepare_checkout(user=buyer)
        revised = services.quote_checkout(user=buyer, discount_code="SAVE")
        current = submit(buyer, revised)
    assert current.pk != old.pk and cart.items.count() == 0
    assert old.items.get().status == OrderItem.Status.CANCELLED
    code.refresh_from_db()
    assert code.used_count == 1

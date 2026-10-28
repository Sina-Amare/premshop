from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import time_machine
from django.core.exceptions import ValidationError

from apps.catalog.models import Plan
from apps.orders import services
from apps.orders.models import DiscountCode, DiscountRedemption, Order, OrderItem, OrderItemEvent
from apps.orders.services import OrderLine

pytestmark = pytest.mark.django_db


def test_creation_debits_finite_stock_and_whole_cancellation_restores_once(buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    order = services.place_order(activation_consent=True, user=buyer, lines=[OrderLine(plan, 2)])
    plan.refresh_from_db()
    assert plan.stock_quantity == 1
    assert order.items.filter(stock_debited=True).count() == 2
    services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    plan.refresh_from_db()
    assert plan.stock_quantity == 3
    assert order.items.filter(status=OrderItem.Status.CANCELLED).count() == 2
    assert OrderItemEvent.objects.filter(order_item__order=order).count() == 4


@pytest.mark.parametrize("origin,current,after", [(None, 5, 5), (2, None, None), (2, 4, 5)])
def test_release_respects_the_original_claim_and_current_stock_policy(
    buyer, plan, origin, current, after
):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=origin)
    order = services.place_order(activation_consent=True, user=buyer, lines=[OrderLine(plan, 1)])
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=current)
    services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    plan.refresh_from_db()
    assert plan.stock_quantity == after


def test_one_unpaid_order_and_exact_deadline_without_retry_extension(buyer, plan):
    with time_machine.travel("2026-10-29 08:00:00", tick=False) as clock:
        first = services.place_order(
            activation_consent=True, user=buyer, lines=[OrderLine(plan, 1)]
        )
        assert first.unpaid_expires_at == first.created_at + timedelta(hours=24)
        with pytest.raises(services.UnpaidOrderExists) as error:
            services.place_order(activation_consent=True, user=buyer, lines=[OrderLine(plan, 1)])
        assert error.value.order.pk == first.pk
        clock.shift(24 * 3600)
        second = services.place_order(
            activation_consent=True, user=buyer, lines=[OrderLine(plan, 1)]
        )
        assert second.pk != first.pk
        assert first.items.get().cancel_reason == OrderItem.CancelReason.EXPIRED_UNPAID
        assert first.unpaid_expires_at == first.created_at + timedelta(hours=24)


def test_expiry_is_repeatable_and_retains_discount_history(buyer, plan):
    code = DiscountCode.objects.create(
        code="SAVE", kind="fixed", value=100, max_uses=1, per_user_limit=1
    )
    with time_machine.travel("2026-10-29 08:00:00", tick=False) as clock:
        order = services.place_order(
            activation_consent=True, user=buyer, lines=[OrderLine(plan, 1)], discount_code="save"
        )
        clock.shift(24 * 3600 - 1)
        assert services.expire_unpaid_orders() == 0
        clock.shift(1)
        assert services.expire_unpaid_orders() == 1
        assert services.expire_unpaid_orders() == 0
        redemption = DiscountRedemption.objects.get(order=order)
        assert redemption.released_at is not None
        code.refresh_from_db()
        assert code.used_count == 0
        other = services.place_order(
            activation_consent=True, user=buyer, lines=[OrderLine(plan, 1)], discount_code="SAVE"
        )
        assert other.discount_amount == 100
        assert DiscountRedemption.objects.count() == 2


@pytest.mark.parametrize(
    "kind,value,price,discount", [("percent", 25, 102, 26), ("fixed", 40, 102, 40)]
)
def test_discount_arithmetic_uses_promotion_and_half_up_rounding(
    buyer, plan, kind, value, price, discount
):
    Plan.objects.filter(pk=plan.pk).update(sale_price=200, promo_price=price)
    DiscountCode.objects.create(code="SAVE", kind=kind, value=value)
    order = services.place_order(
        activation_consent=True, user=buyer, lines=[OrderLine(plan, 1)], discount_code="SAVE"
    )
    assert order.subtotal == price
    assert order.discount_amount == discount
    assert order.total_amount == price - discount
    assert DiscountRedemption.objects.get(order=order).amount == discount


def test_zero_total_and_overflow_do_not_consume_resources(buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=2)
    code = DiscountCode.objects.create(code="FREE", kind="percent", value=100)
    with pytest.raises(ValidationError):
        services.place_order(
            activation_consent=True, user=buyer, lines=[OrderLine(plan, 1)], discount_code=code.code
        )
    Plan.objects.filter(pk=plan.pk).update(sale_price=Decimal("999999999999"))
    with pytest.raises(ValidationError):
        services.place_order(activation_consent=True, user=buyer, lines=[OrderLine(plan, 2)])
    plan.refresh_from_db()
    code.refresh_from_db()
    assert plan.stock_quantity == 2 and code.used_count == 0
    assert not Order.objects.exists() and not DiscountRedemption.objects.exists()


@pytest.mark.parametrize("target", ["OrderItem", "OrderItemEvent", "DiscountRedemption"])
def test_failed_purchase_writes_roll_back_stock_discount_and_history(buyer, plan, target):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=2)
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=100)
    with patch(f"apps.orders.services.{target}.objects.create", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            services.place_order(
                activation_consent=True,
                user=buyer,
                lines=[OrderLine(plan, 1)],
                discount_code=code.code,
            )
    plan.refresh_from_db()
    code.refresh_from_db()
    assert plan.stock_quantity == 2 and code.used_count == 0
    assert not Order.objects.exists() and not OrderItem.objects.exists()
    assert not DiscountRedemption.objects.exists() and not OrderItemEvent.objects.exists()


def test_failed_cancellation_event_rolls_back_the_entire_release(buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=2)
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=100)
    order = services.place_order(
        user=buyer, lines=[OrderLine(plan, 2)], activation_consent=True, discount_code=code.code
    )
    with patch("apps.orders.services.OrderItemEvent.objects.create", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    order.refresh_from_db()
    plan.refresh_from_db()
    code.refresh_from_db()
    assert plan.stock_quantity == 0 and code.used_count == 1
    assert order.cancelled_at is None
    assert order.items.filter(status=OrderItem.Status.PENDING_PAYMENT).count() == 2
    assert DiscountRedemption.objects.get(order=order).released_at is None
    assert OrderItemEvent.objects.count() == 2


@pytest.mark.parametrize("consent", [False, None, 1, "on"])
def test_consent_must_be_explicitly_accepted_before_any_order_is_created(buyer, plan, consent):
    with pytest.raises(ValidationError, match="تأیید کنید"):
        services.place_order(user=buyer, lines=[OrderLine(plan, 1)], activation_consent=consent)
    assert not Order.objects.exists()


@pytest.mark.parametrize("action", ["expiry", "customer"])
def test_legacy_partially_cancelled_order_releases_only_its_remaining_pending_items(
    buyer, plan, action
):
    order = services.place_order(user=buyer, lines=[OrderLine(plan, 2)], activation_consent=True)
    first = order.items.order_by("pk").first()
    OrderItem.objects.filter(pk=first.pk).update(
        status=OrderItem.Status.CANCELLED,
        cancel_reason=OrderItem.CancelReason.CUSTOMER_BEFORE_PAYMENT,
    )
    OrderItemEvent.objects.create(
        order_item=first,
        from_status=OrderItem.Status.PENDING_PAYMENT,
        to_status=OrderItem.Status.CANCELLED,
        actor=OrderItemEvent.Actor.CUSTOMER,
        actor_user=buyer,
    )
    Order.objects.filter(pk=order.pk).update(unpaid_expires_at=order.created_at)
    if action == "expiry":
        assert services.expire_unpaid_orders() == 1
    else:
        services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    assert order.items.filter(status=OrderItem.Status.CANCELLED).count() == 2
    assert first.events.count() == 2
    assert OrderItemEvent.objects.filter(order_item__order=order).count() == 4
    assert services.expire_unpaid_orders() == 0

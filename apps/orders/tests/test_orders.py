from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import (
    DatabaseError,
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.orders.models import DiscountCode, Order, OrderItem, OrderItemEvent
from apps.orders.services import OrderLine, cancel_unpaid_item, place_order

pytestmark = pytest.mark.django_db


def test_multiline_creation_uses_fresh_prices_and_freezes_purchase_facts(buyer, plan):
    other = Plan.objects.create(
        product=plan.product,
        title="سه ماهه",
        cost_price=300000,
        sale_price=600000,
        input_type=Plan.InputType.NONE,
        telegram_required=False,
    )
    Plan.objects.filter(pk=plan.pk).update(sale_price=250000, promo_price=220000)
    order = place_order(user=buyer, lines=[OrderLine(plan, 2), OrderLine(other, 1)])
    assert order.order_number >= 1001
    assert order.subtotal == order.total_amount == Decimal("1040000")
    assert order.discount_amount == 0 and order.discount_code_id is None
    assert order.items.count() == 3
    assert order.items.filter(status=OrderItem.Status.PENDING_PAYMENT).count() == 3
    assert OrderItemEvent.objects.filter(order_item__order=order, from_status=None).count() == 3
    item = order.items.order_by("pk").first()
    before = item.product_snapshot.copy()
    Product.objects.filter(pk=plan.product_id).update(name="نام تازه", warranty="تغییر کرده")
    Plan.objects.filter(pk=plan.pk).update(title="پلن تازه", sale_price=900000, cost_price=800000)
    item.refresh_from_db()
    assert item.price_snapshot == 220000 and item.cost_snapshot == 100000
    assert item.product_snapshot == before
    assert before["name"] == "سرویس نمونه" and before["plan_title"] == "یک ماهه"
    assert before["warranty"] == "days_7"


@pytest.mark.parametrize("quantity", [0, -1, 4, True, "1", 1.5])
def test_invalid_quantity_changes_nothing(buyer, plan, quantity):
    with pytest.raises(ValidationError):
        place_order(user=buyer, lines=[OrderLine(plan, quantity)])
    assert not Order.objects.exists() and not OrderItemEvent.objects.exists()


@pytest.mark.parametrize("target", ["plan", "draft", "unavailable"])
def test_fresh_availability_is_required(buyer, plan, target):
    if target == "plan":
        Plan.objects.filter(pk=plan.pk).update(is_available=False)
    else:
        Product.objects.filter(pk=plan.product_id).update(status=target)
    with pytest.raises(ValidationError):
        place_order(user=buyer, lines=[OrderLine(plan, 1)])
    assert not Order.objects.exists()


def test_empty_duplicate_lines_and_inactive_buyer_are_refused(buyer, plan):
    for lines in ([], [OrderLine(plan, 1), OrderLine(plan, 1)]):
        with pytest.raises(ValidationError):
            place_order(user=buyer, lines=lines)
    User.objects.filter(pk=buyer.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        place_order(user=buyer, lines=[OrderLine(plan, 1)])


def test_required_input_and_quantity_are_checked_from_database(buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(
        input_type="email", requires_customer_input=True, max_quantity=1
    )
    for line in (OrderLine(plan, 1), OrderLine(plan, 2, "target@example.test")):
        with pytest.raises(ValidationError):
            place_order(user=buyer, lines=[line])
    order = place_order(user=buyer, lines=[OrderLine(plan, 1, "target@example.test")])
    assert order.items.get().customer_input == "target@example.test"


def test_failure_writing_creation_event_rolls_back_order_and_items(buyer, plan):
    with patch("apps.orders.services.OrderItemEvent.objects.create", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            place_order(user=buyer, lines=[OrderLine(plan, 2)])
    assert not Order.objects.exists() and not OrderItem.objects.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"subtotal": -1},
        {"discount_amount": -1},
        {"total_amount": -1},
        {"total_amount": 199999},
        {"channel": "unknown"},
    ],
)
def test_database_rejects_invalid_order_facts(buyer, plan, changes):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(**changes)


@pytest.mark.parametrize("code", ["abc", "ABCDEFGHI", "AB-C", "abcd"])
def test_database_rejects_invalid_discount_code(code):
    with pytest.raises(DatabaseError), transaction.atomic():
        DiscountCode.objects.bulk_create([DiscountCode(code=code, kind="fixed", value=100)])


def test_discount_code_normalization_and_uniqueness():
    assert DiscountCode.objects.create(code="save", kind="fixed", value=100).code == "SAVE"
    with pytest.raises(IntegrityError), transaction.atomic():
        DiscountCode.objects.create(code="SAVE", kind="fixed", value=100)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "UNKNOWN"},
        {"price_snapshot": -1},
        {"cost_snapshot": -1},
        {"status": "CANCELLED", "cancel_reason": None},
        {"cancel_reason": "unknown"},
    ],
)
def test_database_rejects_invalid_item_facts(buyer, plan, changes):
    item = place_order(user=buyer, lines=[OrderLine(plan, 1)]).items.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderItem.objects.filter(pk=item.pk).update(**changes)


def test_separate_purchases_remain_independent_and_no_browser_amount_is_accepted(buyer, plan):
    first = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    cancel_unpaid_item(item_id=first.items.get().pk, actor=buyer)
    second = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    assert first.pk != second.pk and first.order_number != second.order_number
    assert first.tracking_token != second.tracking_token
    with pytest.raises(TypeError):
        place_order(user=buyer, lines=[OrderLine(plan, 1)], total_amount=1)


def test_owner_cancel_is_atomic_idempotent_and_attributed(buyer, plan):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    item = order.items.get()
    cancel_unpaid_item(item_id=item.pk, actor=buyer)
    cancel_unpaid_item(item_id=item.pk, actor=buyer)
    item.refresh_from_db()
    assert item.status == OrderItem.Status.CANCELLED
    assert item.cancel_reason == OrderItem.CancelReason.CUSTOMER_BEFORE_PAYMENT
    assert item.events.count() == 2
    event = item.events.order_by("pk").last()
    assert event.from_status == OrderItem.Status.PENDING_PAYMENT
    assert event.actor == "customer" and event.actor_user_id == buyer.pk


def test_other_customer_and_forged_operator_cannot_cancel(buyer, plan):
    item = place_order(user=buyer, lines=[OrderLine(plan, 1)]).items.get()
    other = User.objects.create_user("other@example.test")
    other.is_staff = True
    with pytest.raises(PermissionDenied):
        cancel_unpaid_item(item_id=item.pk, actor=other, note="لغو")
    assert item.events.count() == 1


def test_operator_needs_reason_and_no_paid_state_can_be_cancelled(buyer, plan):
    item = place_order(user=buyer, lines=[OrderLine(plan, 1)]).items.get()
    staff = User.objects.create_user("staff@example.test", is_staff=True)
    with pytest.raises(ValidationError):
        cancel_unpaid_item(item_id=item.pk, actor=staff)
    cancel_unpaid_item(item_id=item.pk, actor=staff, note="درخواست مشتری")
    assert item.events.order_by("pk").last().actor == "operator"
    OrderItem.objects.filter(pk=item.pk).update(status=OrderItem.Status.QUEUED, cancel_reason=None)
    with pytest.raises(ValidationError):
        cancel_unpaid_item(item_id=item.pk, actor=staff, note="لغو")


def test_failure_writing_cancel_event_preserves_pending_state(buyer, plan):
    item = place_order(user=buyer, lines=[OrderLine(plan, 1)]).items.get()
    with patch("apps.orders.services.OrderItemEvent.objects.create", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            cancel_unpaid_item(item_id=item.pk, actor=buyer)
    item.refresh_from_db()
    assert item.status == OrderItem.Status.PENDING_PAYMENT and item.events.count() == 1


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE orders_orderitemevent SET note = 'changed' WHERE id = %s",
        "DELETE FROM orders_orderitemevent WHERE id = %s",
        "DELETE FROM orders_orderitem WHERE id = %s",
        "DELETE FROM orders_order WHERE id = %s",
    ],
)
def test_database_preserves_audit_history(buyer, plan, sql):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    item = order.items.get()
    key = (
        order.pk
        if sql.startswith("DELETE FROM orders_order WHERE")
        else (
            item.pk
            if sql.startswith("DELETE FROM orders_orderitem WHERE")
            else item.events.get().pk
        )
    )
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql, [key])
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert OrderItemEvent.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_cancellation_writes_one_event(buyer, plan):
    item = place_order(user=buyer, lines=[OrderLine(plan, 1)]).items.get()
    barrier = Barrier(2)

    def cancel():
        close_old_connections()
        try:
            actor = User.objects.get(pk=buyer.pk)
            barrier.wait(timeout=10)
            cancel_unpaid_item(item_id=item.pk, actor=actor)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: cancel(), range(2)))
    assert item.events.count() == 2

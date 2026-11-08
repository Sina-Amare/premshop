"""The database's own refusals, written past the services on purpose."""

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import Plan
from apps.orders.models import DiscountCode, OrderItem
from apps.orders.services import OrderLine, place_order
from apps.orders.tests.test_delivery import queued
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import cancel, paid_order
from apps.payments.models import Refund

pytestmark = pytest.mark.django_db


def refused(action):
    with pytest.raises(IntegrityError), transaction.atomic():
        action()


def execute(refund, operator, **changes):
    values = {
        "status": "executed",
        "destination_kind": "card",
        "destination_last4": "6037",
        "bank_ref": "TR-88120",
        "executed_by": operator,
        "executed_at": timezone.now(),
        **changes,
    }
    return Refund.objects.filter(pk=refund.pk).update(**values)


@pytest.fixture
def owed(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    cancel(item, operator)
    return Refund.objects.get(order_item=item)


def test_a_refund_is_never_deleted_owed_or_executed(owed, operator):
    refused(lambda: Refund.objects.filter(pk=owed.pk).delete())
    execute(owed, operator)
    refused(lambda: Refund.objects.filter(pk=owed.pk).delete())
    assert Refund.objects.filter(pk=owed.pk).exists()


def test_an_owed_refund_changes_only_by_being_executed(owed, operator):
    refused(lambda: Refund.objects.filter(pk=owed.pk).update(amount=1))
    refused(lambda: Refund.objects.filter(pk=owed.pk).update(created_at=timezone.now()))
    assert execute(owed, operator) == 1
    owed.refresh_from_db()
    assert (owed.status, owed.amount, owed.bank_ref) == ("executed", 200000, "TR-88120")


def test_an_executed_refund_never_changes(owed, operator):
    execute(owed, operator)
    refused(lambda: Refund.objects.filter(pk=owed.pk).update(bank_ref="TR-1"))
    refused(lambda: Refund.objects.filter(pk=owed.pk).update(status="owed"))


def test_an_execution_records_every_fact(owed, operator):
    refused(lambda: execute(owed, operator, bank_ref=""))
    refused(lambda: execute(owed, operator, destination_last4="60a7"))
    refused(lambda: execute(owed, operator, destination_kind="paypal"))
    refused(lambda: execute(owed, operator, executed_at=None))


def test_an_owed_refund_holds_no_execution_facts(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    refused(
        lambda: Refund.objects.create(
            order_item=item, amount=1000, created_by=operator, bank_ref="TR-1"
        )
    )
    refused(lambda: Refund.objects.create(order_item=item, amount=0, created_by=operator))


def test_an_item_has_one_refund(buyer, plan, operator):
    # Two items, so a second refund of 1 stays under the payment and only the key refuses it.
    _, (item, _) = paid_order(buyer, operator, [OrderLine(plan, 2)])
    Refund.objects.create(order_item=item, amount=1000, created_by=operator)
    with (
        pytest.raises(IntegrityError, match="payments_refund_order_item_id_key"),
        transaction.atomic(),
    ):
        Refund.objects.create(order_item=item, amount=1, created_by=operator)


def test_a_refund_never_exceeds_its_items_paid_amount(buyer, plan, operator):
    # Two items, so 200,001 is over the item but under the order's payment of 400,000.
    _, (item, _) = paid_order(buyer, operator, [OrderLine(plan, 2)])
    refused(lambda: Refund.objects.create(order_item=item, amount=200001, created_by=operator))
    Refund.objects.create(order_item=item, amount=200000, created_by=operator)


def test_an_orders_refunds_never_exceed_its_verified_payment(buyer, plan, operator):
    Plan.objects.filter(pk=plan.pk).update(sale_price=100000)
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=10000)
    order, items = paid_order(buyer, operator, [OrderLine(plan, 3)], "SAVE")
    # A wrong share would let every item refund more than it was paid for.
    OrderItem.objects.filter(pk=items[0].pk).update(discount_share=0)
    Refund.objects.create(order_item=items[0], amount=100000, created_by=operator)
    Refund.objects.create(order_item=items[1], amount=96667, created_by=operator)
    refused(lambda: Refund.objects.create(order_item=items[2], amount=96667, created_by=operator))
    assert order.total_amount == 290000


def test_a_refund_needs_a_verified_payment(buyer, plan, operator):
    item = place_order(user=buyer, lines=[OrderLine(plan, 1)]).items.get()
    refused(lambda: Refund.objects.create(order_item=item, amount=1000, created_by=operator))


def test_an_item_is_refunded_only_with_an_executed_refund(owed, operator, buyer, plan):
    item = owed.order_item
    refused(lambda: OrderItem.objects.filter(pk=item.pk).update(status="REFUNDED"))
    execute(owed, operator)
    OrderItem.objects.filter(pk=item.pk).update(status="REFUNDED")
    _, other = queued(buyer, plan, operator)
    refused(
        lambda: OrderItem.objects.filter(pk=other.pk).update(
            status="REFUNDED", cancel_reason="supply_failure"
        )
    )

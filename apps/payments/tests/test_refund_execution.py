from datetime import timedelta
from decimal import Decimal

import pytest
import time_machine
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.orders import services as orders
from apps.orders.models import OrderItemEvent
from apps.orders.services import OrderLine
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import cancel, paid_order
from apps.payments import services
from apps.payments.models import Refund

pytestmark = pytest.mark.django_db


@pytest.fixture
def owed(buyer, plan, operator):
    order, items = paid_order(buyer, operator, [OrderLine(plan, 3)])
    for item in items:
        cancel(item, operator)
    return order, list(Refund.objects.order_by("pk"))


def execute(order, refunds, operator, **changes):
    return services.execute_refunds(
        **{
            "order_id": order.pk,
            "refund_ids": [refund.pk for refund in refunds],
            "actor": operator,
            "amount": sum((refund.amount for refund in refunds), Decimal(0)),
            "destination_kind": "card",
            "destination_last4": "6037",
            "bank_ref": "transfer-private-98173",
            "executed_at": timezone.now(),
            **changes,
        }
    )


def snapshot(order):
    return (
        list(Refund.objects.values()),
        list(order.items.values_list("status", flat=True)),
        list(OrderItemEvent.objects.values()),
    )


@pytest.mark.parametrize("count", [1, 3])
def test_execution_uses_the_selected_frozen_amounts_and_records_history(owed, operator, count):
    order, refunds = owed
    selected = refunds[:count]
    sent_at = timezone.now()
    with time_machine.travel(sent_at + timedelta(hours=1), tick=False):
        result = execute(order, selected, operator, executed_at=sent_at)
    assert [row.pk for row in result] == [row.pk for row in selected]
    for refund in selected:
        refund.refresh_from_db()
        assert (refund.status, refund.executed_by_id, refund.executed_at) == (
            "executed",
            operator.pk,
            sent_at,
        )
        assert (refund.destination_kind, refund.destination_last4, refund.bank_ref) == (
            "card",
            "6037",
            "transfer-private-98173",
        )
        item = refund.order_item
        item.refresh_from_db()
        event = item.events.latest("pk")
        assert item.status == "REFUNDED"
        assert (event.from_status, event.to_status, event.actor_user_id) == (
            "CANCELLED",
            "REFUNDED",
            operator.pk,
        )
        assert event.created_at == sent_at + timedelta(hours=1)
        assert not event.note
    assert Refund.objects.filter(status="owed").count() == 3 - count


@pytest.mark.parametrize(
    "amount",
    [
        Decimal(0),
        Decimal(-1),
        Decimal("599999"),
        Decimal("600000.1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        600000,
        "600000",
    ],
)
def test_an_invalid_amount_changes_nothing(owed, operator, amount):
    order, refunds = owed
    before = snapshot(order)
    with pytest.raises(ValidationError):
        execute(order, refunds, operator, amount=amount)
    assert snapshot(order) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("bank_ref", ""),
        ("bank_ref", " \t"),
        ("bank_ref", "x" * 65),
        ("bank_ref", None),
        ("destination_kind", "other"),
        ("destination_kind", None),
        ("destination_last4", "123"),
        ("destination_last4", "12345"),
        ("destination_last4", "60a7"),
        ("destination_last4", None),
        ("executed_at", None),
        ("executed_at", "today"),
    ],
)
def test_invalid_execution_facts_change_nothing(owed, operator, field, value):
    order, refunds = owed
    before = snapshot(order)
    with pytest.raises(ValidationError):
        execute(order, refunds, operator, **{field: value})
    assert snapshot(order) == before


def test_the_service_accepts_persian_and_arabic_destination_digits(owed, operator):
    order, refunds = owed
    result = execute(order, refunds[:1], operator, destination_last4="۶٠۳٧", bank_ref="  ref-۱۲  ")
    assert result[0].destination_last4 == "6037"
    assert result[0].bank_ref == "ref-۱۲"


def test_bank_time_is_aware_not_future_and_not_before_any_selected_debt(owed, operator):
    order, refunds = owed
    for when in [
        timezone.now() + timedelta(minutes=1),
        min(row.created_at for row in refunds) - timedelta(seconds=1),
        timezone.now().replace(tzinfo=None),
    ]:
        before = snapshot(order)
        with pytest.raises(ValidationError):
            execute(order, refunds, operator, executed_at=when)
        assert snapshot(order) == before
    # The form displays seconds, while the database also stores fractional seconds.
    when = max(row.created_at for row in refunds).replace(microsecond=0)
    assert len(execute(order, refunds, operator, executed_at=when)) == 3


@pytest.mark.parametrize("ids", [[], [0], [-1], [True], ["1"], "1", None])
def test_invalid_selections_change_nothing(owed, operator, ids):
    order, refunds = owed
    before = snapshot(order)
    with pytest.raises(ValidationError):
        execute(order, refunds, operator, refund_ids=ids)
    assert snapshot(order) == before


def test_duplicate_ids_and_a_refund_from_another_order_are_refused(owed, buyer, plan, operator):
    order, refunds = owed
    other, items = paid_order(buyer, operator, [OrderLine(plan, 1)])
    cancel(items[0], operator)
    foreign = Refund.objects.get(order_item=items[0])
    before = snapshot(order)
    for selected in [[refunds[0].pk, refunds[0].pk], [refunds[0].pk, foreign.pk]]:
        with pytest.raises(ValidationError):
            execute(order, refunds[:2], operator, refund_ids=selected)
        assert snapshot(order) == before
    assert other.pk != order.pk


def test_repeats_and_overlaps_never_execute_the_remaining_selection(owed, operator):
    order, refunds = owed
    execute(order, refunds[:1], operator)
    before = snapshot(order)
    for selected in [refunds[:1], refunds[:2]]:
        with pytest.raises(ValidationError, match="قبلاً"):
            execute(order, selected, operator)
        assert snapshot(order) == before
    execute(order, refunds[1:], operator)
    assert Refund.objects.filter(status="owed").count() == 0


def test_an_event_failure_rolls_back_every_selected_refund(owed, operator, monkeypatch):
    order, refunds = owed
    original = orders._input_event
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("history unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(orders, "_input_event", fail_second)
    before = snapshot(order)
    with pytest.raises(RuntimeError, match="history unavailable"):
        execute(order, refunds, operator)
    assert snapshot(order) == before


@pytest.mark.parametrize(
    "missing", ["change_refund", "change_order", "change_orderitem", "active", "staff"]
)
def test_execution_rechecks_the_stored_operator_and_permissions(owed, operator, missing):
    order, refunds = owed
    actor = User.objects.create_user("limited@example.test", is_staff=True)
    actor.user_permissions.set(
        Permission.objects.filter(
            codename__in=["change_refund", "change_order", "change_orderitem"]
        )
    )
    if missing in {"active", "staff"}:
        User.objects.filter(pk=actor.pk).update(**{"is_" + missing: False})
    else:
        actor.user_permissions.remove(Permission.objects.get(codename=missing))
    before = snapshot(order)
    with pytest.raises(PermissionDenied):
        execute(order, refunds, actor)
    assert snapshot(order) == before


def test_a_fully_discounted_item_has_no_transfer_to_record(buyer, plan, operator):
    from apps.orders.models import DiscountCode
    from apps.orders.tests.test_lifecycle_concurrency import separate_plan

    code = DiscountCode.objects.create(code="FREE", kind="fixed", value=200000, scope="selected")
    code.products.add(plan.product)
    other = separate_plan(plan)
    order, units = paid_order(buyer, operator, [OrderLine(plan, 1), OrderLine(other, 1)], "FREE")
    item = next(row for row in units if row.plan_id == plan.pk)
    cancel(item, operator)
    assert not Refund.objects.filter(order_item=item).exists()
    with pytest.raises(ValidationError):
        execute(order, [], operator, amount=Decimal(0))

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.orders import services
from apps.orders.models import OrderItem, OrderItemEvent
from apps.payments.models import Payment
from apps.payments.services import record_manual_payment

pytestmark = pytest.mark.django_db
TEHRAN = ZoneInfo("Asia/Tehran")
MONDAY = datetime(2026, 11, 9, 10, tzinfo=TEHRAN)
WEDNESDAY = datetime(2026, 11, 11, 12, tzinfo=TEHRAN)


@pytest.fixture
def operator():
    return User.objects.create_superuser("operator@example.test")


def event_id(item):
    return item.events.latest("pk").pk


def pay(order, operator):
    return record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="ref"
    )


def purchase(buyer, plan, operator, *, telegram=True, kind="none"):
    Product.objects.filter(pk=plan.product_id).update(delivery_hours=6)
    plan.product.refresh_from_db()
    plan.telegram_required, plan.input_type, plan.max_quantity = telegram, kind, 1
    plan.save()
    with time_machine.travel(MONDAY, tick=False):
        order = services.place_order(
            user=buyer,
            lines=[services.OrderLine(plan, 1, "target@example.test" if kind == "email" else None)],
        )
        pay(order, operator)
    return order, order.items.get()


def act(service, item, actor, **changes):
    return service(
        order_id=item.order_id,
        item_id=item.pk,
        actor=actor,
        expected_event_id=changes.pop("expected_event_id", event_id(item)),
        **changes,
    )


@pytest.mark.parametrize("kind", ["none", "email"])
def test_telegram_first_receipt_starts_clock_on_wednesday_not_monday(buyer, plan, operator, kind):
    order, item = purchase(buyer, plan, operator, kind=kind)
    assert item.status == "AWAITING_INPUT"
    assert item.due_at is None and item.sla_paused_at is None and item.input_received_at is None
    assert item.input_requested_at == MONDAY
    assert list(item.events.values_list("from_status", "to_status")) == [
        (None, "PENDING_PAYMENT"),
        ("PENDING_PAYMENT", "QUEUED"),
        ("QUEUED", "AWAITING_INPUT"),
    ]
    request_id = event_id(item)
    Product.objects.filter(pk=plan.product_id).update(delivery_hours=1)
    Plan.objects.filter(pk=plan.pk).update(telegram_required=False)
    with time_machine.travel(WEDNESDAY, tick=False):
        act(services.receive_input, item, operator, expected_event_id=request_id)
        act(services.receive_input, item, operator, expected_event_id=request_id)
    item.refresh_from_db()
    assert item.due_at == WEDNESDAY + timedelta(hours=6)
    assert item.input_received_at == WEDNESDAY and item.sla_paused_at is None
    assert item.status == "QUEUED" and item.events.count() == 4
    assert item.input_requested_at == MONDAY
    assert pay(order, operator).status == "verified"
    item.refresh_from_db()
    assert item.due_at == WEDNESDAY + timedelta(hours=6) and item.events.count() == 4


def test_telegram_checkout_email_does_not_complete_activation_input(buyer, plan):
    plan.input_type, plan.telegram_required, plan.max_quantity = "email", True, 1
    plan.save()
    item = services.place_order(
        user=buyer, lines=[services.OrderLine(plan, 1, "target@example.test")]
    ).items.get()
    assert item.input_received_at is None
    assert item.customer_input == "target@example.test"


def test_mixed_payment_commits_each_items_deadline_and_both_telegram_events(buyer, plan, operator):
    telegram = Plan.objects.create(
        product=plan.product,
        title="تلگرام",
        sale_price=100000,
        cost_price=50000,
        input_type="none",
        telegram_required=True,
    )
    with time_machine.travel(MONDAY, tick=False):
        order = services.place_order(
            user=buyer, lines=[services.OrderLine(plan, 1), services.OrderLine(telegram, 1)]
        )
        assert services.can_pay_order(order)
        payment = pay(order, operator)
        assert pay(order, operator).pk == payment.pk
    ordinary, waiting = list(order.items.order_by("pk"))
    assert ordinary.status == "QUEUED" and ordinary.due_at is not None
    assert ordinary.input_requested_at is None and ordinary.sla_paused_at is None
    assert waiting.status == "AWAITING_INPUT" and waiting.due_at is None
    assert waiting.input_requested_at == payment.verified_at and waiting.sla_paused_at is None
    assert ordinary.events.count() == 2 and waiting.events.count() == 3


def test_later_midnight_pauses_shift_stored_deadline_once_and_reject_old_episode(
    buyer, plan, operator
):
    _, item = purchase(buyer, plan, operator, telegram=False)
    original_due = item.due_at
    first_receipt = item.input_received_at
    at = MONDAY + timedelta(hours=13)
    previous = event_id(item)
    with time_machine.travel(at, tick=False):
        act(services.request_input, item, operator, expected_event_id=previous)
    with time_machine.travel(at + timedelta(hours=1), tick=False):
        act(services.request_input, item, operator, expected_event_id=previous)
    item.refresh_from_db()
    assert item.due_at == original_due and item.sla_paused_at == at
    first_request = event_id(item)
    with time_machine.travel(at + timedelta(hours=5), tick=False):
        act(services.resume_input, item, operator, expected_event_id=first_request)
        act(services.resume_input, item, operator, expected_event_id=first_request)
    item.refresh_from_db()
    assert item.due_at == original_due + timedelta(hours=5)
    assert item.input_received_at == first_receipt and item.sla_paused_at is None
    assert item.events.count() == 4
    with pytest.raises(ValidationError):
        act(services.request_input, item, operator, expected_event_id=previous)
    with time_machine.travel(at + timedelta(hours=6), tick=False):
        act(services.request_input, item, operator)
        second_request = event_id(item)
        with pytest.raises(ValidationError):
            act(services.resume_input, item, operator, expected_event_id=first_request)
        act(services.resume_input, item, operator, expected_event_id=second_request)
    item.refresh_from_db()
    assert item.due_at == original_due + timedelta(hours=5) and item.events.count() == 6


@pytest.mark.parametrize("operation", ["request", "receive", "resume"])
def test_input_event_failure_rolls_back_all_fields(buyer, plan, operator, operation):
    _, item = purchase(buyer, plan, operator, telegram=operation == "receive")
    if operation == "resume":
        act(services.request_input, item, operator)
    before = OrderItem.objects.filter(pk=item.pk).values().get()
    count = item.events.count()
    service = getattr(services, f"{operation}_input")
    with patch.object(OrderItemEvent.objects, "create", side_effect=RuntimeError("event failed")):
        with pytest.raises(RuntimeError, match="event failed"):
            act(service, item, operator)
    assert OrderItem.objects.filter(pk=item.pk).values().get() == before
    assert item.events.count() == count


def test_handoff_failure_rolls_back_payment_and_every_item(buyer, plan, operator):
    telegram = Plan.objects.create(
        product=plan.product,
        title="تلگرام",
        sale_price=100000,
        cost_price=50000,
        input_type="none",
        telegram_required=True,
    )
    order = services.place_order(
        user=buyer, lines=[services.OrderLine(plan, 1), services.OrderLine(telegram, 1)]
    )
    before = list(order.items.values())
    original = OrderItemEvent.objects.create

    def fail_request(**kwargs):
        if kwargs.get("to_status") == "AWAITING_INPUT":
            raise RuntimeError("request failed")
        return original(**kwargs)

    with patch.object(OrderItemEvent.objects, "create", side_effect=fail_request):
        with pytest.raises(RuntimeError, match="request failed"):
            pay(order, operator)
    assert not Payment.objects.exists()
    assert list(order.items.values()) == before
    assert OrderItemEvent.objects.filter(order_item__order=order).count() == 2


def test_database_refuses_a_pause_without_a_deadline(buyer, plan):
    item = services.place_order(user=buyer, lines=[services.OrderLine(plan, 1)]).items.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderItem.objects.filter(pk=item.pk).update(sla_paused_at=MONDAY)


@pytest.mark.parametrize("kind", ["other_customer", "staff", "inactive", "stale_permissions"])
def test_receipt_checks_stored_actor_and_ownership(buyer, plan, operator, kind):
    _, item = purchase(buyer, plan, operator)
    actor = User.objects.create_user("other@example.test", is_staff=kind == "staff")
    if kind == "inactive":
        actor = buyer
        User.objects.filter(pk=actor.pk).update(is_active=False)
    if kind == "stale_permissions":
        actor = operator
        User.objects.filter(pk=actor.pk).update(is_superuser=False)
    with pytest.raises(PermissionDenied):
        act(services.receive_input, item, actor)
    assert item.events.count() == 3


def test_owner_receipt_service_can_be_reused_without_allowing_requests(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator)
    with time_machine.travel(WEDNESDAY, tick=False):
        act(services.receive_input, item, buyer)
    event = item.events.latest("pk")
    assert event.actor == "customer" and event.actor_user_id == buyer.pk
    with pytest.raises(PermissionDenied):
        act(services.request_input, item, buyer)
    with pytest.raises(PermissionDenied):
        services.request_input(
            order_id=0, item_id=item.pk, actor=operator, expected_event_id=event_id(item)
        )

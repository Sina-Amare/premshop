from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Plan
from apps.orders.models import DiscountCode, Order, OrderItemEvent
from apps.orders.services import OrderLine, cancel_unpaid_order, expire_unpaid_orders, place_order
from apps.payments import services
from apps.payments.models import Payment

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator():
    return User.objects.create_superuser("operator@example.test")


@pytest.fixture
def order(buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=2)
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=100, max_uses=1)
    return place_order(user=buyer, lines=[OrderLine(plan, 2)], discount_code="SAVE")


def record(order, operator, **changes):
    return services.record_manual_payment(
        **{
            "order_id": order.pk,
            "actor": operator,
            "amount": order.total_amount,
            "bank_ref": "bank-reference-123",
            **changes,
        }
    )


def test_confirmation_uses_saved_total_and_preserves_claims_on_replay(order, operator):
    Plan.objects.filter(pk=order.items.first().plan_id).update(sale_price=1)
    payment = record(order, operator)
    assert payment.method == "manual" and payment.status == "verified"
    assert payment.amount == Decimal("399900") and payment.bank_ref == "bank-reference-123"
    assert payment.matched_by_id == operator.pk and payment.verified_at is not None
    assert record(order, operator).pk == payment.pk
    items = list(order.items.all())
    assert all(item.status == "QUEUED" and item.paid_at == payment.verified_at for item in items)
    events = OrderItemEvent.objects.filter(order_item__order=order, to_status="QUEUED")
    assert events.count() == 2
    assert all(event.actor == "operator" and event.actor_user_id == operator.pk for event in events)
    assert not events.exclude(note="").exists()
    assert Plan.objects.get(pk=items[0].plan_id).stock_quantity == 0
    assert DiscountCode.objects.get(code="SAVE").used_count == 1
    assert not order.discountredemption.released_at
    assert expire_unpaid_orders() == 0
    assert Payment.objects.count() == 1


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("399899"),
        Decimal("399901"),
        Decimal("0"),
        Decimal("1.1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e30"),
        "399900",
        True,
    ],
)
def test_invalid_or_mismatched_amount_writes_nothing(order, operator, amount):
    with pytest.raises(ValidationError):
        record(order, operator, amount=amount)
    assert not Payment.objects.exists()
    assert not order.items.exclude(status="PENDING_PAYMENT").exists()
    assert OrderItemEvent.objects.count() == 2


@pytest.mark.parametrize("reference", ["", "  ", None, "x" * 65])
def test_reference_is_required_and_bounded(order, operator, reference):
    with pytest.raises(ValidationError):
        record(order, operator, bank_ref=reference)
    assert not Payment.objects.exists()


def test_replay_cannot_replace_reference_or_bypass_amount_check(order, operator):
    original = record(order, operator)
    for changes in ({"bank_ref": "different"}, {"amount": Decimal("1")}):
        with pytest.raises(ValidationError):
            record(order, operator, **changes)
    original.refresh_from_db()
    assert original.bank_ref == "bank-reference-123"
    assert Payment.objects.count() == 1
    assert OrderItemEvent.objects.filter(to_status="QUEUED").count() == 2


@pytest.mark.parametrize("state", ["expired", "cancelled", "missing_input", "unknown"])
def test_ineligible_orders_cannot_be_confirmed(order, operator, state):
    if state == "expired":
        Order.objects.filter(pk=order.pk).update(unpaid_expires_at=timezone.now())
    elif state == "cancelled":
        cancel_unpaid_order(order_id=order.pk, actor=operator, note="لغو آزمایشی")
    else:
        item = order.items.first()
        facts = item.product_snapshot.copy()
        if state == "missing_input":
            facts.update(input_type="email", requires_customer_input=True)
        else:
            facts.pop("telegram_required")
        item.product_snapshot = facts
        item.save(update_fields=["product_snapshot"])
    previous = list(order.items.values_list("status", flat=True))
    with pytest.raises(ValidationError):
        record(order, operator)
    assert not Payment.objects.exists()
    assert list(order.items.values_list("status", flat=True)) == previous


def test_collected_input_is_allowed_from_snapshot_not_current_plan(buyer, plan, operator):
    plan.input_type = "email"
    plan.max_quantity = 1
    plan.save()
    order = place_order(user=buyer, lines=[OrderLine(plan, 1, "target@example.test")])
    Plan.objects.filter(pk=plan.pk).update(telegram_required=True)
    assert record(order, operator).status == "verified"


@pytest.mark.parametrize("kind", ["customer", "unpermitted", "inactive", "stale"])
def test_service_checks_stored_active_operator_and_permissions(order, operator, buyer, kind):
    actor = buyer if kind == "customer" else operator
    if kind == "unpermitted":
        actor = User.objects.create_user("staff@example.test", is_staff=True)
    if kind == "inactive":
        User.objects.filter(pk=actor.pk).update(is_active=False)
    if kind == "stale":
        User.objects.filter(pk=actor.pk).update(is_superuser=False, is_staff=False)
    with pytest.raises(PermissionDenied):
        record(order, actor)
    assert not Payment.objects.exists()


def test_scoped_operator_permissions_are_sufficient(order):
    actor = User.objects.create_user("permitted@example.test", is_staff=True)
    for app, codename in (
        ("orders", "change_order"),
        ("orders", "change_orderitem"),
        ("payments", "add_payment"),
    ):
        actor.user_permissions.add(
            Permission.objects.get(content_type__app_label=app, codename=codename)
        )
    assert record(order, actor).matched_by_id == actor.pk


def test_event_failure_rolls_back_payment_items_and_times(order, operator):
    with patch.object(OrderItemEvent.objects, "create", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            record(order, operator)
    assert not Payment.objects.exists()
    assert not order.items.exclude(status="PENDING_PAYMENT", paid_at=None).exists()
    assert Plan.objects.get(pk=order.items.first().plan_id).stock_quantity == 0
    assert DiscountCode.objects.get(code="SAVE").used_count == 1


def test_deadlines_use_saved_hours_and_survive_replay(order, operator, monkeypatch):
    from apps.orders.clock import delivery_deadline

    saved = list(order.items.values_list("product_snapshot__delivery_hours", flat=True))
    product = order.items.first().plan.product
    product.delivery_hours = 1 if saved[0] != 1 else 2
    product.save()
    payment = record(order, operator)
    deadlines = list(order.items.values_list("due_at", flat=True))
    assert deadlines == [
        delivery_deadline(paid_at=payment.verified_at, delivery_hours=hours) for hours in saved
    ]
    monkeypatch.setattr(timezone, "now", lambda: payment.verified_at + timedelta(days=3))
    assert record(order, operator).pk == payment.pk
    assert list(order.items.values_list("due_at", flat=True)) == deadlines


def test_collected_details_have_a_recorded_receipt_and_start_at_payment(buyer, plan, operator):
    from apps.orders.clock import delivery_deadline

    plan.input_type, plan.max_quantity = "email", 1
    plan.save()
    order = place_order(user=buyer, lines=[OrderLine(plan, 1, "target@example.test")])
    item = order.items.get()
    assert item.input_received_at is not None and item.due_at is None
    payment = record(order, operator)
    item.refresh_from_db()
    assert item.input_received_at <= payment.verified_at
    assert item.due_at == delivery_deadline(
        paid_at=payment.verified_at,
        delivery_hours=item.product_snapshot["delivery_hours"],
        requires_input=True,
        input_received_at=item.input_received_at,
    )


def test_deadline_failure_rolls_back_all_financial_and_state_writes(order, operator):
    with patch("apps.orders.services.delivery_deadline", side_effect=RuntimeError("clock failed")):
        with pytest.raises(RuntimeError, match="clock failed"):
            record(order, operator)
    assert not Payment.objects.exists()
    assert not order.items.exclude(status="PENDING_PAYMENT", paid_at=None, due_at=None).exists()
    assert OrderItemEvent.objects.count() == 2


def test_database_blocks_a_second_verified_payment_and_protects_history(order, operator):
    payment = record(order, operator)
    with transaction.atomic(), pytest.raises(IntegrityError):
        Payment.objects.create(
            order=order,
            method="manual",
            status="verified",
            amount=payment.amount,
            bank_ref="another",
            matched_by=operator,
            verified_at=timezone.now(),
        )
    with pytest.raises(ProtectedError):
        order.delete()
    with pytest.raises(ProtectedError):
        operator.delete()


def test_confirmation_entry_checks_payment_and_order_amount(order, operator):
    payment = Payment.objects.create(
        order=order, method="manual", amount=1, bank_ref="ref", matched_by=operator
    )
    with pytest.raises(ValidationError):
        services.confirm_payment(payment_id=payment.pk, actor=operator)
    payment.refresh_from_db()
    assert payment.status == "created" and payment.verified_at is None
    assert not order.items.exclude(status="PENDING_PAYMENT").exists()


def test_replay_after_unpaid_deadline_keeps_original_confirmation(order, operator):
    payment = record(order, operator)
    Order.objects.filter(pk=order.pk).update(unpaid_expires_at=timezone.now() - timedelta(days=1))
    assert record(order, operator).pk == payment.pk
    assert expire_unpaid_orders() == 0

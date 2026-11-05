from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.services import OrderLine, cancel_unpaid_order, place_order
from apps.payments.models import Payment
from apps.payments.services import record_manual_payment

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator():
    return User.objects.create_superuser("operator@example.test")


@pytest.fixture
def order(buyer, plan):
    return place_order(user=buyer, lines=[OrderLine(plan, 1)])


def url(order):
    return reverse("admin:orders_order_record_manual_payment", args=[order.pk])


def test_paid_order_has_no_payment_action(client, order, operator):
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="ref"
    )
    client.force_login(operator)
    response = client.get(reverse("admin:orders_order_change", args=[order.pk]))
    assert f'href="{url(order)}"' not in response.content.decode()


def test_form_get_does_not_write_and_csrf_is_required(order, operator):
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    response = client.get(url(order))
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert 'name="amount"' in response.content.decode()
    assert 'name="bank_ref"' in response.content.decode()
    assert not Payment.objects.exists()
    data = {"amount": "200000", "bank_ref": "private-bank-reference"}
    assert client.post(url(order), data).status_code == 403
    data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    assert client.post(url(order), data).status_code == 302
    assert Payment.objects.get().bank_ref == "private-bank-reference"


@pytest.mark.parametrize(
    "state,allowed",
    [
        ("unpaid", True),
        ("paid", False),
        ("expired", False),
        ("cancelled", False),
        ("telegram", True),
        ("missing_input", False),
        ("lost_input", False),
        ("unknown", False),
        ("created_payment", False),
        ("initiated_payment", False),
        ("empty", False),
        ("queued", False),
        ("invalid_hours", False),
    ],
)
def test_payment_action_and_service_share_eligibility(client, order, operator, state, allowed):
    from django.core.exceptions import ValidationError

    from apps.orders.services import can_pay_order

    if state == "paid":
        record_manual_payment(
            order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="ref"
        )
    elif state == "expired":
        Order.objects.filter(pk=order.pk).update(
            unpaid_expires_at=timezone.now() - timedelta(seconds=1)
        )
    elif state == "cancelled":
        cancel_unpaid_order(order_id=order.pk, actor=operator, note="لغو آزمایشی")
    elif state in {"created_payment", "initiated_payment"}:
        Payment.objects.create(
            order=order,
            method="gateway",
            amount=order.total_amount,
            status="created" if state == "created_payment" else "initiated",
        )
    elif state == "empty":
        order = Order.objects.create(user=order.user, subtotal=0, total_amount=0)
    elif state == "queued":
        order.items.update(status="QUEUED")
    elif state != "unpaid":
        item = order.items.get()
        facts = item.product_snapshot.copy()
        if state == "telegram":
            facts["telegram_required"] = True
        elif state in {"missing_input", "lost_input"}:
            facts.update(input_type="email", requires_customer_input=True)
            if state == "lost_input":
                item.input_received_at = timezone.now()
        elif state == "invalid_hours":
            facts["delivery_hours"] = 0
        else:
            facts.pop("telegram_required")
        item.product_snapshot = facts
        item.save(update_fields=["product_snapshot", "input_received_at"])
    order.refresh_from_db()
    assert can_pay_order(order) is allowed
    client.force_login(operator)
    detail = client.get(reverse("admin:orders_order_change", args=[order.pk]))
    action_url = url(order)
    assert (f'href="{action_url}"' in detail.content.decode()) is allowed
    before = list(order.items.values_list("status", "paid_at", "due_at"))
    if allowed:
        assert (
            client.post(action_url, {"amount": order.total_amount, "bank_ref": "new"}).status_code
            == 302
        )
    elif state != "paid":
        with pytest.raises(ValidationError):
            record_manual_payment(
                order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="new"
            )
        assert list(order.items.values_list("status", "paid_at", "due_at")) == before


@pytest.mark.parametrize("typed", ["۲۰۰,۰۰۰", "200,000", "۲۰۰٬۰۰۰", "۲۰۰،۰۰۰", " 200 000 "])
def test_amount_accepts_the_grouping_the_page_shows(client, order, operator, typed):
    client.force_login(operator)
    response = client.post(url(order), {"amount": typed, "bank_ref": "ref"})
    assert response.status_code == 302
    assert Payment.objects.get().amount == Decimal("200000")


@pytest.mark.parametrize(
    "amount,reference",
    [("199999", "ref"), ("200000.1", "ref"), ("200.000", "ref"), ("200000", ""), ("0", "ref")],
)
def test_form_errors_preserve_data_and_do_not_confirm(client, order, operator, amount, reference):
    client.force_login(operator)
    response = client.post(url(order), {"amount": amount, "bank_ref": reference})
    assert response.status_code == 400 and response.context["form"].errors
    assert response.context["form"].data["amount"] == amount
    assert not Payment.objects.exists()
    assert order.items.get().status == "PENDING_PAYMENT"


def test_customer_sees_queue_without_reference_on_any_public_projection(
    client, order, operator, buyer
):
    reference = "private-bank-reference-unique"
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=Decimal("200000"), bank_ref=reference
    )
    client.force_login(operator)
    detail = reverse("admin:orders_order_change", args=[order.pk])
    assert reference in client.get(detail).content.decode()
    client.force_login(buyer)
    for name, args in (
        ("order-detail", [order.order_number]),
        ("order-list", []),
        ("order-tracking", [order.tracking_token]),
        ("order-invoice", [order.order_number]),
    ):
        response = client.get(reverse(name, args=args))
        assert response.status_code == 200
        assert reference not in response.content.decode()
        if name == "order-detail":
            assert "در صف انجام" in response.content.decode()
            assert "پرداخت آنلاین هنوز فعال نیست" not in response.content.decode()
    assert client.get(url(order)).status_code == 302


def test_unpermitted_staff_cannot_confirm_or_view_bank_reference(client, order, operator):
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=Decimal("200000"), bank_ref="private-ref"
    )
    staff = User.objects.create_user("viewer@example.test", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_order"))
    staff.user_permissions.add(Permission.objects.get(codename="view_orderitem"))
    client.force_login(staff)
    assert client.get(url(order)).status_code == 403
    assert client.post(url(order), {"amount": "200000", "bank_ref": "other"}).status_code == 403
    detail = client.get(reverse("admin:orders_order_change", args=[order.pk]))
    assert detail.status_code == 200 and "private-ref" not in detail.content.decode()


def test_payment_form_and_mismatch_follow_panel_language(client, order, operator):
    client.force_login(operator)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    response = client.get(url(order))
    assert "Amount received (toman)" in response.content.decode()
    assert "Bank reference" in response.content.decode()
    response = client.post(url(order), {"amount": "1", "bank_ref": "ref"})
    assert response.status_code == 400
    assert "The received amount must exactly match the order total." in response.content.decode()

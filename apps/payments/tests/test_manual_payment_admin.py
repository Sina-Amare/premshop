from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.orders.services import OrderLine, place_order
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
    "amount,reference", [("199999", "ref"), ("200000.1", "ref"), ("200000", ""), ("0", "ref")]
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

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.orders import services
from apps.orders.tests.test_input_clock import act, event_id, purchase
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = pytest.mark.django_db


def url(item, action):
    return reverse("admin:orders_order_input_action", args=[item.order_id, item.pk, action])


def test_admin_controls_only_call_services_and_get_never_mutates(client, buyer, plan, operator):
    _, item = purchase(buyer, plan, operator)
    client.force_login(operator)
    detail = reverse("admin:orders_order_change", args=[item.order_id])
    response = client.get(detail)
    assert response.status_code == 200
    assert url(item, "receive") in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert client.get(url(item, "receive")).status_code == 405
    assert item.events.count() == 3
    assert (
        client.post(url(item, "receive"), {"expected_event_id": event_id(item)}).status_code == 302
    )
    item.refresh_from_db()
    assert item.status == "QUEUED" and item.due_at is not None
    assert (
        client.post(url(item, "request"), {"expected_event_id": event_id(item)}).status_code == 302
    )
    item.refresh_from_db()
    assert item.status == "AWAITING_INPUT" and item.sla_paused_at is not None
    request = event_id(item)
    assert client.post(url(item, "resume"), {"expected_event_id": request}).status_code == 302
    assert client.post(url(item, "resume"), {"expected_event_id": request}).status_code == 302
    assert item.events.count() == 6


def test_admin_input_controls_require_csrf_and_valid_episode(buyer, plan, operator):
    _, item = purchase(buyer, plan, operator)
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    client.get(reverse("admin:orders_order_change", args=[item.order_id]))
    data = {"expected_event_id": event_id(item)}
    assert client.post(url(item, "receive"), data).status_code == 403
    data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    data["expected_event_id"] = "bad"
    assert client.post(url(item, "receive"), data).status_code == 400
    data["expected_event_id"] = 0
    assert client.post(url(item, "receive"), data).status_code == 400
    assert item.events.count() == 3


@pytest.mark.parametrize("kind", ["customer", "staff", "mismatched_item"])
def test_admin_input_actions_refuse_unpermitted_accounts_or_objects(
    client, buyer, plan, operator, kind
):
    _, item = purchase(buyer, plan, operator)
    actor = (
        buyer
        if kind == "customer"
        else User.objects.create_user("staff@example.test", is_staff=True)
    )
    client.force_login(operator if kind == "mismatched_item" else actor)
    target = url(item, "receive")
    if kind == "mismatched_item":
        target = reverse(
            "admin:orders_order_input_action", args=[item.order_id, item.pk + 1, "receive"]
        )
    response = client.post(target, {"expected_event_id": event_id(item)})
    assert response.status_code in (302, 403, 404)
    assert item.events.count() == 3


def test_customer_sees_request_and_support_without_private_metadata(client, buyer, plan, operator):
    order, item = purchase(buyer, plan, operator)
    client.force_login(buyer)
    response = client.get(reverse("order-detail", args=[order.order_number]))
    body = response.content.decode()
    assert "اطلاعات فعال‌سازی را در تلگرام برای پشتیبانی بفرستید." in body
    assert "https://t.me/premshop_support" in body
    assert "expected_event_id" not in body and 'name="customer_input"' not in body
    act(services.receive_input, item, operator)
    response = client.get(reverse("order-detail", args=[order.order_number]))
    assert "اطلاعات فعال‌سازی را در تلگرام برای پشتیبانی بفرستید." not in response.content.decode()

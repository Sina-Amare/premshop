import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db


def test_order_admin_is_read_only_searchable_and_never_discloses_input(
    client, buyer, plan, settings
):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1, "private-target@example.test")])
    client.force_login(User.objects.create_superuser("operator@example.test"))
    settings.FIELD_ENCRYPTION_KEY = ""
    listing = reverse("admin:orders_order_changelist")
    for query in (str(order.order_number), buyer.email):
        response = client.get(listing, {"q": query})
        assert response.status_code == 200 and order in response.context["cl"].result_list
        assert "private-target@example.test" not in response.content.decode()
    detail = reverse("admin:orders_order_change", args=[order.pk])
    response = client.get(detail)
    assert response.status_code == 200
    text = response.content.decode()
    assert "private-target@example.test" not in text and "customer_input" not in text
    assert "سرویس نمونه" in text and "سابقه‌ی سفارش" in text
    assert 'name="_save"' not in text
    assert 'class="order-timeline"' in text
    assert "مشخصات سفارش" in text and "مبلغ‌های سفارش" in text
    assert buyer.email in text
    assert client.post(detail, {"total_amount": 1}).status_code == 403
    assert client.get(reverse("admin:orders_order_add")).status_code == 403
    assert client.post(reverse("admin:orders_order_delete", args=[order.pk])).status_code == 403
    assert Order.objects.get(pk=order.pk).total_amount == 200000


def test_customer_and_staff_without_permission_cannot_inspect_orders(client, buyer, plan):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    detail = reverse("admin:orders_order_change", args=[order.pk])
    assert client.get(detail).status_code == 302
    client.force_login(buyer)
    assert client.get(detail).status_code == 302
    client.force_login(User.objects.create_user("staff@example.test", is_staff=True))
    assert client.get(detail).status_code == 403

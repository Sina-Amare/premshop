from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.orders.models import Order
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db


def test_order_admin_is_read_only_searchable_and_never_discloses_input(
    client, buyer, plan, settings
):
    Plan.objects.filter(pk=plan.pk).update(
        input_type="email", requires_customer_input=True, max_quantity=1
    )
    order = place_order(
        user=buyer,
        lines=[OrderLine(plan, 1, "private-target@example.test")],
    )
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


def test_operator_sees_live_expiry_backlog_and_stale_sweep_warning(client, buyer, plan):
    from datetime import timedelta

    from django.utils import timezone

    from apps.orders.models import UnpaidExpirySweep

    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    Order.objects.filter(pk=order.pk).update(unpaid_expires_at=timezone.now())
    client.force_login(User.objects.create_superuser("operator@example.test"))
    url = reverse("admin:orders_order_changelist")
    response = client.get(url)
    assert response.context["expiry_stale"] is True
    assert response.context["expiry_backlog"] == 1
    UnpaidExpirySweep.objects.create(
        last_success_at=timezone.now(), processed_count=0, overdue_count=0
    )
    response = client.get(url)
    assert response.context["expiry_stale"] is False
    assert response.context["expiry_backlog"] == 1
    UnpaidExpirySweep.objects.update(last_success_at=timezone.now() - timedelta(minutes=4))
    assert "بررسی خودکار به‌روز نیست" in client.get(url).content.decode()


def test_admin_order_list_query_count_is_the_same_for_two_orders_and_six(client, plan, queries_for):
    """Every order has three items on three products, so a lookup of an item or
    of its product per row would show."""

    def add(start, stop):
        for i in range(start, stop):
            customer = User.objects.create_user(f"listed-{i}@example.test")
            plans = []
            for j in range(3):
                product = Product.objects.create(
                    category=plan.product.category,
                    name=f"محصول {i}-{j}",
                    slug=f"listed-{i}-{j}",
                    status=Product.Status.ACTIVE,
                )
                plans.append(
                    Plan.objects.create(
                        product=product,
                        title="۱ ماهه",
                        cost_price=Decimal("1"),
                        sale_price=Decimal("200000"),
                        max_quantity=3,
                        input_type=Plan.InputType.NONE,
                        telegram_required=False,
                    )
                )
            place_order(user=customer, lines=[OrderLine(p, 1) for p in plans])

    client.force_login(User.objects.create_superuser("operator@example.test"))
    listing = reverse("admin:orders_order_changelist")
    add(0, 2)
    few, response = queries_for(listing)
    assert len(response.context["cl"].result_list) == 2
    add(2, 6)

    many, response = queries_for(listing)

    assert len(response.context["cl"].result_list) == 6
    assert many == few

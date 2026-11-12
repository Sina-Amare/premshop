import re

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.core.formatting import to_persian_digits
from apps.orders.selectors import item_numbers
from apps.orders.services import OrderLine, place_order
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import cancel, paid_order

pytestmark = pytest.mark.django_db


def test_the_panel_names_an_item_by_its_place_in_its_order(client, buyer, plan, operator):
    plan.input_type, plan.telegram_required, plan.max_quantity = "none", False, 3
    plan.save()
    # A decoy order first, so no item's database id is also its place.
    place_order(user=User.objects.create_user("decoy@example.test"), lines=[OrderLine(plan, 3)])
    order, items = paid_order(buyer, operator, [OrderLine(plan, 3)])
    assert item_numbers([order.pk]) == {item.pk: place for place, item in enumerate(items, 1)}
    first, _, third = items
    cancel(third, operator)
    ids = {to_persian_digits(item.pk) for item in items}
    client.force_login(operator)
    for url in [
        reverse("admin:orders_order_deliver", args=[order.pk, first.pk]),
        reverse("admin:orders_order_change", args=[order.pk]),
        reverse("admin:orders_order_reconciliation"),
    ]:
        response = client.get(url)
        assert response.status_code == 200, url
        named = set(re.findall("آیتم ([۰-۹]+)", response.content.decode()))
        assert named and named <= {"۱", "۲", "۳"}, (url, named)
    form = client.get(reverse("admin:orders_order_execute_refunds", args=[order.pk]))
    choices = re.findall(r"\(([۰-۹]+)\) · ", form.content.decode())
    assert choices == ["۳"] and not set(choices) & ids


def test_numbering_items_costs_the_same_queries_for_one_refund_and_three(
    client, buyer, plan, operator, queries_for
):
    plan.input_type, plan.telegram_required, plan.max_quantity = "none", False, 3
    plan.save()
    order, items = paid_order(buyer, operator, [OrderLine(plan, 3)])
    cancel(items[0], operator)
    client.force_login(operator)
    pages = [
        reverse("admin:orders_order_change", args=[order.pk]),
        reverse("admin:orders_order_reconciliation"),
    ]
    one = [queries_for(url)[0] for url in pages]
    for item in items[1:]:
        cancel(item, operator)
    three = [queries_for(url)[0] for url in pages]
    assert three == one

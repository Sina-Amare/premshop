"""The order list's three groups: each order sits under the state it leads with."""

import re

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.core.formatting import to_persian_digits
from apps.orders import views
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_order_detail import order_for_state

pytestmark = pytest.mark.django_db(transaction=True)


def listed(client, query=""):
    body = client.get(reverse("order-list") + query).content.decode()
    nav = re.search(r'<nav class="order-tabs".*?</nav>', body, re.S)
    numbers = re.findall(r"<h2>سفارش (.+?)</h2>", body)
    return body, nav.group(0) if nav else "", numbers


def tabs(nav):
    return [
        (re.sub(r"<[^>]+>|\s+", " ", label).strip(), "aria-current" in attrs)
        for attrs, label in re.findall(r"<a([^>]*)>(.*?)</a>", nav, re.S)
    ]


def numbers_of(*orders):
    return {to_persian_digits(order.order_number) for order in orders}


def test_each_order_sits_under_the_state_it_leads_with(client, buyer, plan, operator):
    # An open unpaid order refuses a second one, so it is placed last.
    built = {
        state: order_for_state(buyer, plan, operator, state)
        for state in ("expired", "refunded", "delivered", "queued", "mixed_refund", "unpaid")
    }
    client.force_login(buyer)
    _, nav, unpaid = listed(client)
    assert tabs(nav) == [
        ("در انتظار پرداخت ۱", True),
        ("در جریان ۲", False),
        ("تحویل‌شده ۱", False),
        ("لغوشده ۲", False),
    ]
    assert set(unpaid) == numbers_of(built["unpaid"])
    _, nav, current = listed(client, "?tab=current")
    assert tabs(nav)[1] == ("در جریان ۲", True)
    # A delivered product beside a refund still owed keeps the order in progress.
    assert set(current) == numbers_of(built["queued"], built["mixed_refund"])
    _, _, delivered = listed(client, "?tab=delivered")
    assert set(delivered) == numbers_of(built["delivered"])
    _, _, closed = listed(client, "?tab=cancelled")
    assert set(closed) == numbers_of(built["refunded"], built["expired"])


def test_the_list_opens_on_the_first_group_that_holds_an_order(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "delivered")
    client.force_login(buyer)
    _, nav, numbers = listed(client)
    assert tabs(nav) == [
        ("در انتظار پرداخت ۰", False),
        ("در جریان ۰", False),
        ("تحویل‌شده ۱", True),
        ("لغوشده ۰", False),
    ]
    assert set(numbers) == numbers_of(order)
    body, _, numbers = listed(client, "?tab=current")
    assert numbers == [] and "سفارشی در جریان ندارید." in body
    _, nav, _ = listed(client, "?tab=../everything")
    assert tabs(nav)[2] == ("تحویل‌شده ۱", True)


def test_a_customer_without_orders_sees_no_groups(client, buyer):
    client.force_login(buyer)
    body, nav, numbers = listed(client)
    assert nav == "" and numbers == []
    assert "هنوز سفارشی ثبت نکرده‌اید." in body


def test_the_counts_hold_only_the_customers_own_orders(client, buyer, plan, operator):
    order_for_state(buyer, plan, operator, "queued")
    other = User.objects.create_user("other@example.test")
    hidden = order_for_state(other, plan, operator, "delivered")
    client.force_login(buyer)
    body, nav, _ = listed(client, "?tab=delivered")
    assert tabs(nav) == [
        ("در انتظار پرداخت ۰", False),
        ("در جریان ۱", False),
        ("تحویل‌شده ۰", True),
        ("لغوشده ۰", False),
    ]
    assert to_persian_digits(hidden.order_number) not in body


def test_a_page_of_a_group_links_to_its_next_page_in_the_same_group(
    client, buyer, plan, operator, monkeypatch
):
    monkeypatch.setattr(views, "ORDERS_PER_PAGE", 1)
    for _ in range(2):
        order_for_state(buyer, plan, operator, "delivered")
    client.force_login(buyer)
    body, _, numbers = listed(client, "?tab=delivered")
    assert len(numbers) == 1
    assert 'href="?tab=delivered&amp;page=2"' in body

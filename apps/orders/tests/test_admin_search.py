"""Finding things in the panel without scrolling."""

import json

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.core.formatting import to_persian_digits
from apps.notifications.models import Notification
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(client):
    user = User.objects.create_superuser("finder@example.test")
    client.force_login(user)
    return user


def test_discount_products_are_picked_from_a_searchable_list(client, operator):
    body = client.get(reverse("admin:orders_discountcode_add")).content.decode()
    assert 'class="selectfilter"' in body and 'data-field-name="' in body


def test_notifications_are_found_by_order_number_or_recipient(client, operator, buyer, plan):
    other = User.objects.create_user("other@example.test")
    notes = {}
    for user in (buyer, other):
        order = place_order(user=user, lines=[OrderLine(plan, 1)])
        notes[user] = Notification.objects.create(
            order=order,
            dedupe_key=f"finder-{user.pk}",
            recipient=user.email,
            subject="s",
            text_body="t",
            html_body="h",
        )
    url = reverse("admin:notifications_notification_changelist")
    for term in (to_persian_digits(notes[buyer].order.order_number), buyer.email):
        found = list(client.get(url, {"q": term}).context["cl"].result_list)
        assert found == [notes[buyer]], term


def test_the_product_picker_speaks_persian(client, operator):
    catalog = client.get(reverse("admin:jsi18n")).content.decode()
    # The catalog is JSON inside JavaScript, so its Persian arrives escaped.
    assert json.dumps("Choose all %s") + ": " + json.dumps("افزودن همه‌ی %s") in catalog

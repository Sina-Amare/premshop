"""Card previews share public plan prices without exposing operator data."""

import re
from datetime import date

import pytest
from django.urls import reverse

from apps.catalog.models import Product
from apps.catalog.tests.test_cards import offers as offers

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("destination", ["home", "category", "search", "product"])
def test_quick_plans_are_deduplicated_and_keep_public_prices(client, offers, destination):
    offers.plans.update(cost_price=87654321, supplier_url="https://private.example/source")
    offers.plans.create(
        title="پلن قدیمی", cost_price=1, sale_price=999999, is_available=False, sort_order=9
    )
    Product.objects.create(
        category=offers.category, name="Hidden service", slug="hidden-service", status="draft"
    )
    url = {
        "home": reverse("home"),
        "category": reverse("category", args=[offers.category.slug]),
        "search": reverse("search") + "?q=Claude",
        "product": reverse("product", args=[offers.slug]),
    }[destination]
    body = client.get(url).content.decode()
    assert body.count(f'data-quick-product="{offers.pk}"') == 1
    pool = body.split('<dialog id="quick-plans"', 1)[1].split("</dialog>", 1)[0]
    assert "۱,۴۵۰,۰۰۰" in pool and "۳,۷۸۰,۰۰۰" in pool
    assert "پلن قدیمی" in pool and "فعلاً ناموجود" in pool
    assert "Hidden service" not in pool
    assert "87654321" not in pool and "private.example" not in pool
    assert reverse("product", args=[offers.slug]) in pool
    assert "<form" not in pool and "افزودن به سبد" not in pool
    assert not any("<button" in a for a in re.findall(r"<a\b[^>]*>.*?</a>", body, re.S))


def test_customer_metadata_is_removed_but_delivery_and_operator_records_remain(client, offers):
    offers.facts_checked_on = date(2026, 10, 24)
    offers.save(update_fields=["facts_checked_on"])
    body = client.get(reverse("product", args=[offers.slug])).content.decode()
    assert "جزئیات تحویل" not in body
    assert "قیمت‌ها بررسی‌شده" not in body and "آخرین بررسی:" not in body
    assert 'id="delivery"' in body and "زمان تحویل" in body
    offers.refresh_from_db()
    assert offers.facts_checked_on == date(2026, 10, 24)
    assert all(p.price_confirmed_at is not None for p in offers.plans.all())

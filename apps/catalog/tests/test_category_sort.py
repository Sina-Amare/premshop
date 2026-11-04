"""A category page can be ordered by newest, cheapest or dearest.

The price is the one the card shows: the lowest effective price among the
plans that can be bought, with a promotion counted only while its window is
open.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product
from apps.catalog.tests.test_views import CATEGORY_PAGE_QUERY_BUDGET

pytestmark = pytest.mark.django_db

LABELS = {"new": "جدیدترین", "cheap": "ارزان‌ترین", "expensive": "گران‌ترین"}


@pytest.fixture
def category():
    return Category.objects.create(name="هوش مصنوعی", slug="ai")


def make(category, slug, *prices, days_old, promo=None, ended=False, available=True):
    """A product with one plan per price, created `days_old` days ago.

    `promo` is a price offered on the first plan: open-ended, or already over
    when `ended`.
    """
    obj = Product.objects.create(
        category=category,
        name=slug,
        slug=slug,
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=Product.Status.ACTIVE,
    )
    Product.objects.filter(pk=obj.pk).update(created_at=timezone.now() - timedelta(days=days_old))
    for position, price in enumerate(prices):
        offer = promo if position == 0 else None
        Plan.objects.create(
            product=obj,
            title=f"پلن {position}",
            cost_price=Decimal(1),
            sale_price=Decimal(price),
            promo_price=None if offer is None else Decimal(offer),
            promo_ends_at=timezone.now() - timedelta(days=1) if offer and ended else None,
            is_available=available,
            sort_order=position,
        )
    return obj


@pytest.fixture
def shelf(category):
    """Prices as the card shows them, and ages in days:

    b 100k (a promotion open now, over a 500k list price)   5 days
    c 200k (a promotion that ended, so the 200k list price) 3 days
    d 250k (the cheaper of two plans)                       2 days
    a 300k                                                  1 day
    f 300k (ties with a)                                    6 days
    e no plan that can be bought                            4 days
    """
    make(category, "a", 300000, days_old=1)
    make(category, "b", 500000, days_old=5, promo=100000)
    make(category, "c", 200000, days_old=3, promo=50000, ended=True)
    make(category, "d", 400000, 250000, days_old=2)
    make(category, "e", 100000, days_old=4, available=False)
    make(category, "f", 300000, days_old=6)
    return category


def names(response):
    return [product.slug for product in response.context["products"]]


def sorted_page(client, category, sort=None):
    query = {} if sort is None else {"sort": sort}
    response = client.get(reverse("category", args=[category.slug]), query)
    assert response.status_code == 200
    return response


def test_the_default_and_new_put_the_newest_product_first(client, shelf):
    newest = ["a", "d", "c", "e", "b", "f"]

    assert names(sorted_page(client, shelf)) == newest
    assert names(sorted_page(client, shelf, "new")) == newest


def test_cheap_orders_by_the_lowest_effective_price_and_puts_the_unbuyable_last(client, shelf):
    assert names(sorted_page(client, shelf, "cheap")) == ["b", "c", "d", "a", "f", "e"]


def test_expensive_is_the_reverse_price_order_with_the_unbuyable_still_last(client, shelf):
    assert names(sorted_page(client, shelf, "expensive")) == ["a", "f", "d", "c", "b", "e"]


@pytest.mark.parametrize("value", ["", "bogus", "CHEAP", "cheap ", "price", "1"])
def test_an_unknown_value_falls_back_to_the_default_without_an_error(client, shelf, value):
    default = names(sorted_page(client, shelf))

    response = sorted_page(client, shelf, value)

    assert names(response) == default
    assert current(response.content.decode()) == ["new"]


def test_the_sort_uses_one_clock_for_every_plan(client, shelf, monkeypatch):
    """A promotion that ends mid-request must not price one plan with it and
    its sibling without."""
    from apps.catalog import views

    seen = []
    real = views.effective_price

    def recording(plan, at=None):
        seen.append(at)
        return real(plan, at)

    monkeypatch.setattr(views, "effective_price", recording)

    sorted_page(client, shelf, "cheap")

    assert seen and all(moment is not None for moment in seen)
    assert len(set(seen)) == 1


def links(body):
    """Each sort link as (href, label, aria-current), in page order."""
    control = re.search(r'<div class="sort".*?</div>', body, re.S)
    assert control, "the page has no sort control"
    return re.findall(
        r'<a class="sort__link" href="([^"]*)"(?: (aria-current="true"))?>([^<]+)</a>',
        control.group(0),
    )


def current(body):
    by_label = {label: key for key, label in LABELS.items()}
    return [by_label[label] for _, flag, label in links(body) if flag]


def test_the_control_is_plain_links_with_the_chosen_one_marked(client, shelf):
    base = reverse("category", args=[shelf.slug])

    for sort, expected in (
        (None, "new"),
        ("new", "new"),
        ("cheap", "cheap"),
        ("expensive", "expensive"),
    ):
        body = sorted_page(client, shelf, sort).content.decode()
        assert [(href, label) for href, _, label in links(body)] == [
            (base, LABELS["new"]),
            (f"{base}?sort=cheap", LABELS["cheap"]),
            (f"{base}?sort=expensive", LABELS["expensive"]),
        ]
        assert current(body) == [expected]


def test_the_canonical_address_stays_free_of_the_parameter(client, shelf, settings):
    settings.SITE_URL = "https://premshop.ir"
    address = f"https://premshop.ir{reverse('category', args=[shelf.slug])}"

    for sort in ("cheap", "expensive", "bogus"):
        body = sorted_page(client, shelf, sort).content.decode()
        assert f'<link rel="canonical" href="{address}">' in body
        assert f'<meta property="og:url" content="{address}">' in body
        assert "sort=" not in re.search(r'<link rel="canonical"[^>]*>', body).group(0)


def test_a_category_of_one_product_has_nothing_to_order(client, category):
    make(category, "only", 100000, days_old=1)

    body = sorted_page(client, category, "cheap").content.decode()

    assert 'class="sort"' not in body and "sort__link" not in body


@pytest.mark.parametrize("sort", [None, "new", "cheap", "expensive"])
def test_every_order_stays_inside_the_category_query_budget(client, category, sort):
    for i in range(12):
        make(category, f"p{i}", 100000 + i * 1000, 90000 + i * 1000, days_old=i, promo=80000)

    with CaptureQueriesContext(connection) as queries:
        response = sorted_page(client, category, sort)

    assert len(response.context["products"]) == 12
    assert len(queries) <= CATEGORY_PAGE_QUERY_BUDGET, [q["sql"][:80] for q in queries]


def test_the_sort_control_is_labelled_as_a_display_order(client, shelf):
    body = sorted_page(client, shelf).content.decode()

    assert ">نمایش به ترتیب:<" in body
    assert "مرتب‌سازی" not in body

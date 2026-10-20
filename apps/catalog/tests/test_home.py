"""The home page's rate sheet: every department on one dated sheet, one clock in its head."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product
from apps.core.formatting import format_jalali

pytestmark = pytest.mark.django_db


def stock(category: Category, slug: str, **plan) -> Product:
    product = Product.objects.create(
        category=category,
        name=slug,
        slug=slug,
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(
        product=product, title="۱ ماهه", cost_price=Decimal(1), sale_price=Decimal(200000), **plan
    )
    return product


def test_discounts_in_two_departments_that_end_together_share_one_clock(client):
    """The head speaks for every row under it, so the shared end is found across
    the whole sheet, not department by department."""
    end = timezone.now() + timedelta(days=3)
    for name, slug in (("هوش مصنوعی", "ai"), ("سرگرمی", "fun")):
        category = Category.objects.create(name=name, slug=slug)
        stock(category, slug, promo_price=Decimal(150000), promo_ends_at=end)

    body = client.get(reverse("home")).content.decode()

    assert body.count('class="rs-group"') == 2, "the control: two departments, two groups"
    assert body.count('class="sheet__note"') == 1
    assert body.count('class="badge badge--time countdown"') == 1
    assert "rs__clock" not in body


def test_the_sheet_is_dated_by_its_stalest_price(client):
    ai = Category.objects.create(name="هوش مصنوعی", slug="ai")
    stale = timezone.localtime() - timedelta(days=9)
    Plan.objects.filter(product=stock(ai, "old")).update(price_confirmed_at=stale)
    stock(ai, "new")

    body = client.get(reverse("home")).content.decode()

    assert f'بررسی‌شده از <time datetime="{stale:%Y-%m-%d}">{format_jalali(stale)}</time>' in body

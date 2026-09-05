"""The catalog's rules: one price function, two CHECKs, and what a visitor may see."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product
from apps.catalog.pricing import effective_price

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def product():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    return Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        short_description="اشتراک کلاود پرو",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        status=Product.Status.ACTIVE,
    )


def plan(product, **kwargs) -> Plan:
    kwargs.setdefault("title", "۱ ماهه")
    kwargs.setdefault("cost_price", Decimal("900000"))
    kwargs.setdefault("sale_price", Decimal("1450000"))
    return Plan.objects.create(product=product, **kwargs)


# --- The one price rule -----------------------------------------------------


def test_effective_price_inside_window_returns_promo(product):
    p = plan(
        product,
        promo_price=Decimal("1200000"),
        promo_starts_at=NOW - timedelta(days=1),
        promo_ends_at=NOW + timedelta(days=1),
    )
    assert effective_price(p, at=NOW) == Decimal("1200000")
    assert p.promotion_is_active


def test_effective_price_outside_window_and_unset_returns_sale_price(product):
    ended = plan(
        product,
        promo_price=Decimal("1200000"),
        promo_starts_at=NOW - timedelta(days=2),
        promo_ends_at=NOW - timedelta(days=1),
    )
    not_yet = plan(
        product,
        title="۳ ماهه",
        promo_price=Decimal("1200000"),
        promo_starts_at=NOW + timedelta(days=1),
        promo_ends_at=NOW + timedelta(days=2),
    )
    unset = plan(product, title="۱۲ ماهه")

    assert effective_price(ended, at=NOW) == Decimal("1450000")
    assert effective_price(not_yet, at=NOW) == Decimal("1450000")
    assert effective_price(unset, at=NOW) == Decimal("1450000")
    assert not unset.promotion_is_active


@pytest.mark.parametrize(
    "starts, ends",
    [
        (None, None),  # both open: a promotion until further notice
        (None, NOW + timedelta(days=1)),  # no start: already started
        (NOW - timedelta(days=1), None),  # no end: never ends
    ],
)
def test_effective_price_open_ended_bounds(product, starts, ends):
    p = plan(product, promo_price=Decimal("1000000"), promo_starts_at=starts, promo_ends_at=ends)
    assert effective_price(p, at=NOW) == Decimal("1000000")


def test_the_window_is_half_open(product):
    """A promotion ending at 10:00 is over at 10:00, not at 10:00:59."""
    end = NOW
    p = plan(product, promo_price=Decimal("1000000"), promo_ends_at=end)
    assert effective_price(p, at=end - timedelta(seconds=1)) == Decimal("1000000")
    assert effective_price(p, at=end) == Decimal("1450000")


# --- The two CHECKs, proven at the database, not the form ---------------------


@pytest.mark.parametrize(
    "bad", [Decimal("0"), Decimal("-5"), Decimal("1450000"), Decimal("2000000")]
)
def test_promo_price_check_rejects_zero_negative_and_above_sale_price(product, bad):
    with pytest.raises(IntegrityError), transaction.atomic():
        plan(product, promo_price=bad)


def test_promo_window_check_rejects_start_after_end(product):
    with pytest.raises(IntegrityError), transaction.atomic():
        plan(
            product,
            promo_price=Decimal("1000000"),
            promo_starts_at=NOW,
            promo_ends_at=NOW - timedelta(hours=1),
        )


def test_the_checks_reach_the_admin_form_in_persian(product):
    """Django validates CheckConstraints in full_clean, so the admin shows the
    database's rule as a Persian sentence — no custom clean() to drift from it."""
    from django.core.exceptions import ValidationError

    p = Plan(product=product, title="x", cost_price=1, sale_price=1000, promo_price=1000)
    with pytest.raises(ValidationError) as excinfo:
        p.full_clean()
    assert "قیمت تخفیفی" in str(excinfo.value)


# --- What a visitor may see ---------------------------------------------------


def test_draft_product_hidden(product):
    Product.objects.create(
        category=product.category,
        name="پیش‌نویس",
        slug="draft",
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=Product.Status.DRAFT,
    )
    Product.objects.create(
        category=product.category,
        name="ناموجود",
        slug="gone",
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=Product.Status.UNAVAILABLE,
    )

    assert list(Product.objects.public()) == [product]


def test_search_text_is_maintained_on_save(product):
    assert product.search_text == "claude pro اشتراک کلاود پرو"
    product.name = "Claude Max"
    product.save()
    assert product.search_text.startswith("claude max")


def test_search_finds_arabic_keyboard_input(product):
    assert list(Product.objects.public().search("كلاود")) == [product]  # arabic kaf
    assert list(Product.objects.public().search("CLAUDE")) == [product]
    assert list(Product.objects.public().search("")) == []


def test_slug_is_filled_from_the_name_when_blank():
    category = Category.objects.create(name="ابزار توسعه")
    assert category.slug == "ابزار-توسعه"
    product = Product.objects.create(
        category=category, name="GitHub Copilot", delivery_type="on_customer_account"
    )
    assert product.slug == "github-copilot"


def test_str(product):
    p = plan(product)
    assert str(product) == "Claude Pro"
    assert str(p) == "Claude Pro — ۱ ماهه"
    assert str(product.category) == "هوش مصنوعی"


def test_a_category_with_products_cannot_be_deleted(product):
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        product.category.delete()

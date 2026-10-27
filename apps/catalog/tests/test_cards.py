"""A product's starting price and another plan's offer are distinct facts."""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product
from apps.catalog.templatetags.prices import card_price_steps

pytestmark = pytest.mark.django_db


@pytest.fixture
def offers():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        status=Product.Status.ACTIVE,
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
    )
    Plan.objects.create(product=product, title="۱ ماهه", cost_price=1, sale_price=1450000)
    Plan.objects.create(
        product=product,
        title="۳ ماهه",
        cost_price=1,
        sale_price=4200000,
        promo_price=3780000,
        promo_ends_at=timezone.now() + timedelta(days=2),
        sort_order=1,
    )
    return product


def test_a_non_cheapest_plan_offer_is_visible_on_every_card(offers):
    body = render_to_string("catalog/_card.html", {"product": offers})
    assert "۳ ماهه" in body and "۱۰٪ تخفیف" in body
    assert "۱,۴۵۰,۰۰۰" in body and "۱ ماهه" in body
    assert "<del" not in body


def test_home_counts_products_with_a_discount_on_any_available_plan(client, offers):
    response = client.get(reverse("home"))
    assert [p.pk for p in response.context["discounted"]] == [offers.pk]


@pytest.mark.parametrize("remaining", [0, 2])
def test_sold_out_choices_keep_their_row_without_advertising_a_price(
    client, offers, remaining, django_assert_num_queries
):
    offers.plans.update(stock_quantity=remaining)
    sold = offers.plans.get(title="۳ ماهه")
    offers.plans.filter(pk=sold.pk).update(stock_quantity=0, promo_price=50)
    product = Product.objects.select_related("category").prefetch_related("plans").get(pk=offers.pk)
    with django_assert_num_queries(0):
        steps = card_price_steps(product)
        assert not product.discounted_plans
        assert [step["price"] for step in steps] == ([Decimal(1450000)] if remaining else [])
        quick = render_to_string("catalog/_quick_plans.html", {"quick_plan_products": [product]})
        card = render_to_string("catalog/_card.html", {"product": product})
    assert "۳ ماهه" in quick and "فعلاً ناموجود" in quick
    sold_row = next(row for row in re.findall(r"<li>.*?</li>", quick, re.S) if "۳ ماهه" in row)
    assert "۵۰" not in sold_row and "تخفیف" not in quick
    if not remaining:
        assert "ناموجود" in card
    assert not client.get(reverse("home")).context["discounted"]


def test_unavailable_expired_and_future_offers_do_not_count(offers):
    promotion = offers.plans.get(title="۳ ماهه")
    for fields in (
        {"is_available": False},
        {"is_available": True, "promo_ends_at": timezone.now() - timedelta(seconds=1)},
        {"promo_ends_at": None, "promo_starts_at": timezone.now() + timedelta(days=1)},
    ):
        offers.plans.filter(pk=promotion.pk).update(**fields)
        assert offers.discounted_plans == []


def test_card_offers_are_ordered_by_discount_and_keep_prefetched_queries(
    offers, django_assert_num_queries
):
    stronger = offers.plans.create(
        title="۶ ماهه", cost_price=1, sale_price=8000000, promo_price=6400000
    )
    prefetched = Product.objects.prefetch_related("plans").get(pk=offers.pk)
    with django_assert_num_queries(0):
        assert prefetched.discounted_plans[0].pk == stronger.pk
        assert prefetched.discounted_plans[0].discount_percent == 20
        assert prefetched.cheapest_plan.effective_price == Decimal("1450000")


@pytest.mark.parametrize("value", ["#10a37f", "#ABCDEF", ""])
def test_brand_color_accepts_only_optional_hex_colors(value):
    field = Product._meta.get_field("brand_colour")
    assert field.clean(value, None) == value


@pytest.mark.parametrize("value", ["red", "#fff", "#123456;display:none", "#12345g"])
def test_brand_color_refuses_css_injection_and_invalid_colors(value):
    with pytest.raises(ValidationError):
        Product._meta.get_field("brand_colour").clean(value, None)


def test_invalid_stored_color_is_not_interpolated_into_css(offers):
    offers.brand_colour = "#123456;display:none"
    body = render_to_string("catalog/_card.html", {"product": offers})
    assert "display:none" not in body
    assert "--brand:" in body


def test_a_scheduled_offer_selected_at_another_expiry_also_expires(offers):
    now = timezone.now()
    offers.plans.all().delete()
    offers.plans.create(
        title="اول",
        cost_price=1,
        sale_price=300,
        promo_price=90,
        promo_ends_at=now + timedelta(hours=2),
    )
    offers.plans.create(
        title="بعدی",
        cost_price=1,
        sale_price=200,
        promo_price=80,
        promo_starts_at=now + timedelta(hours=1),
        promo_ends_at=now + timedelta(hours=3),
    )
    assert [step["price"] for step in card_price_steps(offers)] == [90, 80, 200]


def test_the_starting_plan_has_no_dot_that_reads_as_a_persian_zero(offers):
    body = render_to_string("catalog/_card.html", {"product": offers})
    assert "·" not in body

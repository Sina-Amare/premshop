"""The public catalog as a visitor meets it: pages, states, budgets, leaks."""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product, ProductSpec
from apps.core import ratelimit

pytestmark = pytest.mark.django_db

PRODUCT_PAGE_QUERY_BUDGET = 15


@pytest.fixture(autouse=True)
def clean_cache(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def category():
    return Category.objects.create(name="هوش مصنوعی", slug="ai", description="ابزارهای هوش مصنوعی")


@pytest.fixture
def product(category):
    obj = Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        short_description="اشتراک کلاود پرو",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        region=Product.Region.GLOBAL,
        warranty=Product.Warranty.FULL_PERIOD,
        delivery_hours=12,
        status=Product.Status.ACTIVE,
    )
    for i in range(6):
        ProductSpec.objects.create(
            product=obj, title=f"ویژگی {i}", value=f"مقدار {i}", sort_order=i
        )
    Plan.objects.create(
        product=obj,
        title="۱ ماهه",
        cost_price=Decimal("900000"),
        sale_price=Decimal("1450000"),
        promo_price=Decimal("1200000"),
        promo_ends_at=timezone.now() + timedelta(days=7),
        requires_customer_input=True,
        customer_input_label="ایمیل شما",
        sort_order=0,
    )
    for i, title in enumerate(["۳ ماهه", "۶ ماهه", "۱۲ ماهه"], start=1):
        Plan.objects.create(
            product=obj,
            title=title,
            cost_price=Decimal("1000000"),
            sale_price=Decimal(str(1450000 * (i + 1))),
            sort_order=i,
        )
    Plan.objects.create(
        product=obj,
        title="قدیمی",
        cost_price=Decimal("1"),
        sale_price=Decimal("1000"),
        is_available=False,
        sort_order=9,
    )
    return obj


def make_product(category, slug, status=Product.Status.ACTIVE, **plan) -> Product:
    obj = Product.objects.create(
        category=category,
        name=slug,
        slug=slug,
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=status,
    )
    Plan.objects.create(
        product=obj,
        title="۱ ماهه",
        cost_price=Decimal("100"),
        sale_price=plan.get("sale_price", Decimal("200000")),
        promo_price=plan.get("promo_price"),
    )
    return obj


# --- Pages render, in every state ------------------------------------------


def test_home_renders_categories_and_products(client, product):
    body = client.get(reverse("home")).content.decode()

    assert "Claude Pro" in body
    assert "هوش مصنوعی" in body
    assert "۱ محصول" in body, "category counts render in Persian digits"


def test_home_empty_state_is_designed_not_blank(client):
    body = client.get(reverse("home")).content.decode()

    assert "هنوز محصولی ثبت نشده است" in body


def test_category_page_and_its_empty_states(client, category, product):
    body = client.get(reverse("category", args=[category.slug])).content.decode()
    assert "Claude Pro" in body

    filtered = client.get(
        reverse("category", args=[category.slug]) + "?type=gift_card"
    ).content.decode()
    assert "Claude Pro" not in filtered
    assert "محصولی با این نوع تحویل نداریم" in filtered

    empty = Category.objects.create(name="خالی", slug="empty")
    body = client.get(reverse("category", args=[empty.slug])).content.decode()
    assert "فعلاً محصولی در این دسته نیست" in body


def test_unknown_category_and_product_are_404(client):
    assert client.get("/c/nope/").status_code == 404
    assert client.get("/p/nope/").status_code == 404


def test_draft_product_hidden_everywhere(client, category):
    draft = make_product(category, "draft", status=Product.Status.DRAFT)
    gone = make_product(category, "gone", status=Product.Status.UNAVAILABLE)

    assert client.get(reverse("product", args=[draft.slug])).status_code == 404
    assert client.get(reverse("product", args=[gone.slug])).status_code == 404
    home = client.get(reverse("home")).content.decode()
    assert "draft" not in home and "gone" not in home
    listing = client.get(reverse("category", args=[category.slug])).content.decode()
    assert "draft" not in listing
    found = client.get(reverse("search") + "?q=draft").content.decode()
    assert 'href="/p/draft/"' not in found


# --- The product page ---------------------------------------------------------


def test_product_page_shows_region_warranty_and_delivery_in_the_title_area(client, product):
    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "ریجن جهانی" in body
    assert "تا پایان دوره" in body
    assert "تحویل تا ۱۲ ساعت" in body


def test_product_page_query_count(client, product):
    """Four plans and six specs must not become ten queries; a real product has more."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("product", args=[product.slug]))

    assert response.status_code == 200
    assert len(queries) <= PRODUCT_PAGE_QUERY_BUDGET, [q["sql"][:80] for q in queries]


def test_category_page_query_count_does_not_grow_with_products(client, category):
    for i in range(12):
        make_product(category, f"p{i}")

    with CaptureQueriesContext(connection) as queries:
        client.get(reverse("category", args=[category.slug]))

    assert len(queries) <= 8, "the card grid must prefetch plans, not query per card"


def test_add_to_cart_control_disabled_for_unavailable_plan(client, product):
    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert body.count('name="plan"') == 5, "every plan renders, available or not"
    assert "فعلاً ناموجود" in body
    old_plan = product.plans.get(title="قدیمی")
    assert re.search(
        rf'value="{old_plan.id}"\s+disabled', body
    ), "the unavailable plan's radio is disabled"
    assert "افزودن به سبد" in body
    assert (
        'data-requires-input="1"' in body
    ), "the customer-input plan is marked so the stepper caps at 1"


def test_product_with_no_available_plan_disables_the_button(client, category):
    obj = make_product(category, "sold-out")
    obj.plans.update(is_available=False)

    body = client.get(reverse("product", args=[obj.slug])).content.decode()

    assert ">ناموجود</button>" in body
    assert "این محصول فعلاً موجود نیست" in body


def test_catalog_card_and_product_page_render_struck_through_original(client, product):
    """The promo is on the cheapest plan, so the card shows the pair; the product
    page shows it on that plan's row. The original is muted and struck, never amber."""
    listing = client.get(reverse("category", args=[product.category.slug])).content.decode()
    page = client.get(reverse("product", args=[product.slug])).content.decode()

    for body in (listing, page):
        assert '<span class="price__was">۱,۴۵۰,۰۰۰</span>' in body
        assert "۱,۲۰۰,۰۰۰" in body
        assert "badge--time" not in body, "a promotion is not a countdown"


def test_prices_render_persian_digits_and_ascii_thousands_separator(client, product):
    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "۱,۲۰۰,۰۰۰" in body
    assert "1,200,000" not in body
    assert "۱٬۲۰۰٬۰۰۰" not in body, "U+066C reads as a rendering fault"


def test_seo_fields_render_when_set(client, product):
    product.seo_title = "خرید اشتراک کلاود پرو"
    product.seo_description = "کلاود پرو با تحویل ۱۲ ساعته"
    product.save()

    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "<title>خرید اشتراک کلاود پرو — PremShop</title>" in body
    assert 'content="کلاود پرو با تحویل ۱۲ ساعته"' in body


# --- Search ---------------------------------------------------------------------


def test_search_finds_sloppy_arabic_keyboard_input(client, product):
    body = client.get(reverse("search") + "?q=كلاود").content.decode()  # arabic kaf

    assert "Claude Pro" in body
    assert "۱ محصول" in body


def test_search_miss_is_a_designed_empty_state(client, product):
    body = client.get(reverse("search") + "?q=xyzzy").content.decode()

    assert "پیدا نشد" in body
    assert reverse("home") in body


def test_search_rate_limited(client, product):
    for _ in range(ratelimit.SEARCH_PER_IP.times):
        assert client.get(reverse("search") + "?q=a").status_code == 200

    response = client.get(reverse("search") + "?q=a")

    assert response.status_code == 429
    assert "جستجوهای پشت سر هم زیاد شد" in response.content.decode()


def test_an_empty_query_does_not_spend_the_search_budget(client):
    for _ in range(ratelimit.SEARCH_PER_IP.times + 5):
        assert client.get(reverse("search")).status_code == 200

"""The public catalog as a visitor meets it: pages, states, budgets, leaks."""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product, ProductSpec
from apps.core import ratelimit

pytestmark = pytest.mark.django_db

PRODUCT_PAGE_QUERY_BUDGET = 15


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


def test_product_page_shows_warranty_and_delivery_in_the_title_area(client, product):
    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "تا پایان دوره" in body
    assert "تحویل تا ۱۲ ساعت" in body


def test_region_is_not_a_property_of_every_product(client, product):
    """Removed on the owner's ruling 2026-10-13: a product that genuinely is
    region-bound says so in a spec row the operator adds, like any other fact."""
    from apps.catalog.models import ProductSpec

    ProductSpec.objects.create(product=product, title="ریجن", value="ترکیه", sort_order=0)
    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "جهانی" not in body
    assert "ترکیه" in body  # the spec row, which is the only way a region appears now


def test_one_search_box_per_page(client, product):
    """Three of them used to be on screen at once: the header's, the home hero's,
    and the search page's own."""
    for url in [
        reverse("home"),
        reverse("search") + "?q=کلاود",
        reverse("product", args=[product.slug]),
    ]:
        body = client.get(url).content.decode()
        assert body.count('name="q"') == 1, url


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


# --- Quantity and what the plan asks for, both set by the operator ----------


def test_no_quantity_control_unless_the_operator_asked_for_one(client, product):
    """Most of this shop is a subscription activated on one person's account,
    where «how many» is not a question the buyer has — and a stepper on it
    invites an order nobody can deliver. The control is absent from the markup
    rather than disabled, so a visitor with JavaScript off never meets a
    stepper that cannot do anything."""
    assert all(plan.max_quantity == 1 for plan in product.plans.all())

    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert 'name="quantity"' not in body
    assert "تعداد" not in body


def test_the_quantity_control_appears_when_a_plan_takes_more_than_one(client, product):
    plan = product.plans.filter(is_available=True, requires_customer_input=False).first()
    plan.max_quantity = 5
    plan.save()

    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert 'name="quantity"' in body
    assert 'max="5"' in body, "the ceiling is the operator's, not a hard-coded 10"
    assert f'data-max="{plan.max_quantity}"' in body


def test_each_plan_carries_its_own_ceiling_and_its_own_question(client, product):
    """Two plans on one product can differ: a gift card takes five, the
    subscription beside it takes one. The script reads both off the radio."""
    body = client.get(reverse("product", args=[product.slug])).content.decode()

    asking = product.plans.get(requires_customer_input=True)
    assert f'data-asks="{asking.customer_input_label}"' in body
    assert 'data-max="1"' in body


def test_a_plan_that_asks_for_customer_details_cannot_be_ordered_twice(product):
    """One box to type the email into means one per order. The buy box has
    always enforced this in JavaScript; the database enforces it now, so the
    admin, a shell and an import cannot get around it."""
    plan = product.plans.get(requires_customer_input=True)
    plan.max_quantity = 3

    with pytest.raises(IntegrityError):
        plan.save()


# --- Facts the operator writes, and decides the weight of ------------------


def test_nothing_is_printed_beside_the_title_unless_the_operator_said_so(client, product):
    """Capacity, device count, region, a condition that applies to one product
    and nothing else — none of these is true of every product, so none is a
    column. A product nobody has thought about shows what it always showed."""
    assert not product.specs.filter(is_key=True).exists()

    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "badge--key" not in body


def test_a_key_spec_is_printed_beside_the_title_and_still_in_the_table(client, product):
    ProductSpec.objects.create(
        product=product, title="نوع اکانت", value="اشتراکی، ۴ نفره", is_key=True, sort_order=-1
    )

    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert 'class="badge badge--key">نوع اکانت: <bdi>اشتراکی، ۴ نفره</bdi>' in body
    # And it has not left the specification table, which is still the full list.
    assert body.count("اشتراکی، ۴ نفره") == 2


def test_a_key_spec_value_is_isolated(client, product):
    """Spec values are operator-written and routinely carry a Latin run or a
    number, which is where bidi reorders a line."""
    ProductSpec.objects.create(product=product, title="ریجن", value="US", is_key=True)

    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "<bdi>US</bdi>" in body


@pytest.mark.parametrize("bad", [0, 11])
def test_a_quantity_ceiling_outside_one_to_ten_is_refused(product, bad):
    plan = product.plans.filter(requires_customer_input=False).first()
    plan.max_quantity = bad

    with pytest.raises(IntegrityError):
        plan.save()


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
    """It used to be a card saying «پیدا نشد» with a link home. It is the
    catalogue now: with ten products the shelf is a better answer than a
    sentence, and the heading takes the blame in the first person the way
    every Iranian shop surveyed does."""
    body = client.get(reverse("search") + "?q=xyzzy").content.decode()

    assert "نداریم" in body
    assert product.name in body, "the shelf should be on the page"
    assert "پیدا نشد" not in body, "the passive puts the miss on the visitor"


def test_search_rate_limited(client, product):
    for _ in range(ratelimit.SEARCH_PER_IP.times):
        assert client.get(reverse("search") + "?q=a").status_code == 200

    response = client.get(reverse("search") + "?q=a")

    assert response.status_code == 429
    assert "جستجوهای پشت سر هم زیاد شد" in response.content.decode()


def test_an_empty_query_does_not_spend_the_search_budget(client):
    for _ in range(ratelimit.SEARCH_PER_IP.times + 5):
        assert client.get(reverse("search")).status_code == 200

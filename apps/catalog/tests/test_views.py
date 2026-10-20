"""The public catalog as a visitor meets it: pages, states, budgets, leaks."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product, ProductSpec
from apps.catalog.pricing import effective_price
from apps.core import ratelimit, support
from apps.core.formatting import format_toman, toman_in_words

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
    assert (
        'class="dept__meta"><span>۱ سرویس</span>' in body
    ), "category counts render in Persian digits"


def test_home_empty_state_is_designed_not_blank(client):
    body = client.get(reverse("home")).content.decode()

    assert "فعلاً محصولی برای فروش نداریم" in body


def test_category_page_and_its_empty_state(client, category, product):
    body = client.get(reverse("category", args=[category.slug])).content.decode()
    assert "Claude Pro" in body
    assert "۱ سرویس" in body

    empty = Category.objects.create(name="خالی", slug="empty")
    body = client.get(reverse("category", args=[empty.slug])).content.decode()
    assert "در این دسته فعلاً سرویسی نداریم" in body


def test_a_category_page_offers_no_filter(client, category, product):
    """Surveyed at eleven Iranian shops: account type is a facet on none of
    them and delivery method on none, and of six small shops with eight
    products or fewer, zero offer attribute filters. With two or three
    products in a category every filter returns everything or nothing. The
    chips also printed the enum's own labels at the customer, including one
    that described the buyer in the third person."""
    body = client.get(reverse("category", args=[category.slug])).content.decode()

    assert 'class="chip' not in body
    assert "نوع تحویل" not in body
    # The query string is no longer read at all, so it cannot hide the shelf.
    filtered = client.get(
        reverse("category", args=[category.slug]) + "?type=gift_card"
    ).content.decode()
    assert "Claude Pro" in filtered


def test_no_page_describes_the_buyer_in_the_third_person(client, product):
    """«روی اکانت خود مشتری» is how an operator describes a buyer to
    themselves. The label reads «فعال‌سازی روی اکانت شخصی», which also reads
    right in the panel, where «شما» would be the operator."""
    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "خود مشتری" not in body


def test_renewal_is_stated_only_when_it_is_true(client, product):
    """«اگر نوشته شده باشه میشه» is only honest while absence claims nothing,
    so there is no grey «قابل تمدید نیست» — and none on a gift card, which
    has no period to renew."""
    assert not product.renewable
    body = client.get(reverse("product", args=[product.slug])).content.decode()
    assert "تمدید" not in body

    product.renewable = True
    product.save()
    body = client.get(reverse("product", args=[product.slug])).content.decode()
    assert "روی همین اکانت، با قیمت روز" in body


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

    # The answers index under the title states each fact once.
    index = body.split('class="facts product__facts"', 1)[1].split("</nav>", 1)[0]
    assert (
        '<span class="facts__k">گارانتی</span><span class="facts__v">تا پایان دوره</span>' in index
    )
    assert '<span class="facts__k">تحویل</span><span class="facts__v">تا ۱۲ ساعت</span>' in index


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
    assert '<span class="plan__off">ناموجود</span>' in body
    old_plan = product.plans.get(title="قدیمی")
    assert re.search(
        rf'value="{old_plan.id}"\s+disabled', body
    ), "the unavailable plan's radio is disabled"
    assert "سفارش در تلگرام" in body


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


def test_the_invoice_announces_through_its_total_alone(client, product):
    """The quantity was an <output>, a live region of its own, so one press of
    «+» was announced twice: the quantity, then the total."""
    plan = product.plans.filter(is_available=True, requires_customer_input=False).first()
    plan.max_quantity = 5
    plan.save()

    body = client.get(reverse("product", args=[product.slug])).content.decode()
    invoice = body.split('id="invoice"', 1)[1].split("</section>", 1)[0]

    assert 'name="quantity"' in invoice, "the control: the quantity row is on the page"
    assert "<output" not in invoice and 'role="status"' not in invoice
    assert invoice.count("aria-live=") == 1
    assert '<div class="invoice__total" aria-live="polite" aria-atomic="true">' in invoice


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

    assert (
        '<span class="badge badge--key"><span class="badge__k">نوع اکانت</span>'
        "<bdi>اشتراکی، ۴ نفره</bdi></span>" in body
    )
    # And it has not left the specification table, which is still the full list.
    assert body.count("اشتراکی، ۴ نفره") == 2


def test_a_key_spec_value_is_isolated(client, product):
    """Spec values are operator-written and routinely carry a Latin run or a
    number, which is where bidi reorders a line."""
    ProductSpec.objects.create(product=product, title="ریجن", value="US", is_key=True)

    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert '<bdi lang="en">US</bdi>' in body


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

    assert re.search(r'<button type="button" class="[^"]*invoice__cta" disabled[ >]', body)
    assert ">خرید فعلاً ممکن نیست</button>" in body
    assert "این محصول موجود نیست." in body
    assert f'href="{reverse("category", args=[category.slug])}"' in body.split('id="invoice"')[1]
    assert "invoice__total" not in body, "no total for nothing to buy"


# Attributes allowed after the class: a discount with an end carries data-left.
PRICE_COMPONENT = re.compile(r'<span class="price[^"]*"[^>]*>.*?</span></span>', re.S)


def test_catalog_card_and_product_page_render_struck_through_original(client, product):
    """The promo is on the cheapest plan, so the card shows the pair; the product
    page shows it on that plan's row. The original is muted and struck, never amber.

    The assertion is scoped to the price component. It used to search the whole
    page for `badge--time`, which read as "a promotion is not a countdown" and
    was wrong twice over: amber is exactly what a countdown is for
    (design-language.md, Colour), and the rule it meant to pin is only that the
    *figure* is never amber.

    `<del>` and not `<s>`: to a crawler that runs no CSS the two figures read
    as one impossible number unless the markup says the first was removed."""
    listing = client.get(reverse("category", args=[product.category.slug])).content.decode()
    page = client.get(reverse("product", args=[product.slug])).content.decode()

    for body in (listing, page):
        assert (
            '<del class="price__was"><span class="visually-hidden">قیمت قبل: </span>۱,۴۵۰,۰۰۰</del>'
            in body
        )
        assert "۱,۲۰۰,۰۰۰" in body
        prices = PRICE_COMPONENT.findall(body)
        assert any("price__was" in markup for markup in prices), "the discounted one was read"
        for markup in prices:
            assert "badge--time" not in markup, "the figure itself is never amber"


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

    assert "<title>خرید اشتراک کلاود پرو | PremShop</title>" in body
    assert 'content="کلاود پرو با تحویل ۱۲ ساعته"' in body


# --- Grouping on the home page, and what to show next on a product page ----


def sheet_groups(body: str) -> list[str]:
    return [h.strip() for h in re.findall(r'class="rs-group__title"[^>]*>([^<]+)<', body)]


def test_home_groups_products_under_their_department(client, category, product):
    other = Category.objects.create(name="گیفت کارت", slug="gift", sort_order=1)
    make_product(other, "steam-gift")

    body = client.get(reverse("home")).content.decode()

    assert sheet_groups(body) == ["هوش مصنوعی", "گیفت کارت"], "one per department, in order"
    assert "۲ سرویس در ۲ دسته" in body, "with a count that has its unit, not a bare digit"
    assert body.count('class="dept__link"') == 2, "each department links to its own page"
    assert 'class="dept__link" href="/c/gift/"' in body


def test_every_product_on_the_home_page_lands_in_a_group(client, category, product):
    """A product belongs to exactly one department, so a grid grouped by
    department must still show all of them. The group list and the grid are
    built from two separate queries; if those two filters ever drift apart, a
    product disappears from the home page and nothing says so."""
    make_product(Category.objects.create(name="گیفت کارت", slug="gift"), "steam-gift")
    make_product(category, "copilot")

    response = client.get(reverse("home"))
    grouped = [p.pk for _, items in response.context["by_category"] for p in items]

    assert sorted(grouped) == sorted(p.pk for p in response.context["featured"])
    assert len(grouped) == len(set(grouped)), "and in exactly one group, not two"


def test_an_empty_department_gets_no_heading(client, category, product):
    Category.objects.create(name="خالی", slug="empty")

    body = client.get(reverse("home")).content.decode()

    assert "خالی" not in body


def test_a_department_the_cap_cut_off_gets_no_heading(client, category, product, monkeypatch):
    """The grid is capped and the department list is not, so once the
    catalogue outgrows one screen a department can have products and still
    have nothing to show here. A heading over an empty grid is the visible
    form of that."""
    monkeypatch.setattr("apps.catalog.views.HOME_PRODUCT_CAP", 1)
    make_product(Category.objects.create(name="گیفت کارت", slug="gift"), "steam-gift")

    response = client.get(reverse("home"))
    body = response.content.decode()

    shown = list(response.context["featured"])
    assert len(shown) == 1
    assert sheet_groups(body) == [shown[0].category.name]
    assert body.count('class="dept__link"') == 1, "nor a place in the departments index"


def test_product_page_suggests_other_products(client, category, product):
    sibling = make_product(category, "copilot")
    elsewhere = make_product(Category.objects.create(name="گیفت کارت", slug="gift"), "steam-gift")

    body = client.get(reverse("product", args=[product.slug])).content.decode()
    suggested = body.split("محصولات دیگر", 1)[1]

    assert f'href="/p/{sibling.slug}/"' in suggested
    assert f'href="/p/{elsewhere.slug}/"' in suggested, "a thin department is topped up"
    assert f'href="/p/{product.slug}/"' not in suggested, "never suggest the page you are on"


def test_suggestions_put_the_same_department_first(client, category, product):
    """Ranked, not shuffled: the department the visitor is already in, then a
    live discount, then the rest. Nothing here knows the visitor — there are
    no orders yet to learn from — so the page must not imply that it does."""
    gifts = Category.objects.create(name="گیفت کارت", slug="gift")
    make_product(gifts, "discounted-gift", promo_price=Decimal("100000"))
    sibling = make_product(category, "copilot")

    response = client.get(reverse("product", args=[product.slug]))

    assert response.context["related"][0].pk == sibling.pk
    assert "پیشنهاد" not in response.content.decode(), "no claim of personalisation"


def test_suggestions_are_capped_and_cost_a_fixed_number_of_queries(client, category, product):
    for i in range(9):
        make_product(category, f"p{i}")

    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("product", args=[product.slug]))

    assert len(response.context["related"]) == 4
    assert len(queries) <= PRODUCT_PAGE_QUERY_BUDGET, [q["sql"][:80] for q in queries]


def test_the_places_a_department_leaves_go_to_a_discount_and_then_to_the_newest(
    client, category, product
):
    """Topping up a thin department took whichever products sorted first by
    name, so a discount elsewhere lost its place to an older product called «a…»."""
    for i in range(3):
        make_product(category, f"sibling-{i}")
    gifts = Category.objects.create(name="گیفت کارت", slug="gift")
    make_product(gifts, "a-plain")
    make_product(gifts, "z-discounted", promo_price=Decimal("100000"))

    related = client.get(reverse("product", args=[product.slug])).context["related"]
    assert [p.slug for p in related][3] == "z-discounted"

    Product.objects.filter(slug="z-discounted").update(status=Product.Status.DRAFT)
    make_product(gifts, "z-newest")

    related = client.get(reverse("product", args=[product.slug])).context["related"]
    assert [p.slug for p in related][3] == "z-newest"


# --- When a product arrives ------------------------------------------------


def test_delivery_time_falls_back_to_the_hours(client, category, product):
    body = client.get(reverse("product", args=[product.slug])).content.decode()

    assert "تا ۱۲ ساعت" in body
    assert product.delivery_display == "تا ۱۲ ساعت"


def test_an_operator_note_replaces_the_hours_everywhere_it_is_shown(client, category, product):
    """A number cannot say «slower this week because the gateway is flaky».
    The note replaces the display on every surface at once — the rate sheet's
    row, the answers index, the invoice and the page's own question — because
    they all read one property rather than formatting the field themselves."""
    note = "۴۸ تا ۷۲ ساعت"
    product.delivery_note = note
    product.save()

    page = client.get(reverse("product", args=[product.slug])).content.decode()
    listing = client.get(reverse("category", args=[category.slug])).content.decode()

    shown = {
        "the answers index": page.split('class="facts product__facts"', 1)[1].split("</nav>", 1)[0],
        "the question": page.split('id="delivery"', 1)[1].split("</div>", 1)[0],
        "the invoice": page.split('id="invoice"', 1)[1].split("</section>", 1)[0],
    }
    for where, part in shown.items():
        assert note in part, where
    assert f'<td class="rs__eta">{note}</td>' in listing, "and the rate sheet's row"
    assert "تا ۱۲ ساعت" not in page, "the hours must not survive beside the note"
    assert "تا ۱۲ ساعت" not in listing


def test_a_latin_name_in_the_note_keeps_its_place_wherever_it_is_shown(client, category, product):
    """The answers index and the question isolated «Gmail» in the note while the
    invoice, the rate sheet and the comparison printed it bare, so on one page
    the same phrase read in two orders."""
    make_product(category, "second")
    product.delivery_note = "تا ۲ ساعت برای Gmail"
    product.save()

    page = client.get(reverse("product", args=[product.slug])).content.decode()
    listing = client.get(reverse("category", args=[category.slug])).content.decode()

    shown = 'تا ۲ ساعت برای <bdi lang="en">Gmail</bdi>'
    assert shown in page.split('id="invoice"', 1)[1].split("</section>", 1)[0], "the invoice"
    assert f'<td class="rs__eta">{shown}</td>' in listing, "the rate sheet"
    assert f'<td class="cmp__eta">{shown}</td>' in listing, "the comparison"


def test_the_note_never_touches_the_real_deadline(product):
    """The operator panel counts down from `delivery_hours`, and the 72-hour
    promise is a database CHECK on it. A note is display only, so the number
    it overrides stays exactly where the deadline arithmetic will look."""
    product.delivery_note = "هر وقت رسید"
    product.save()
    product.refresh_from_db()

    assert product.delivery_hours == 12


# --- The clock on a discount -----------------------------------------------


def test_a_discount_with_a_deadline_shows_how_long_is_left(client, category):
    obj = make_product(category, "sale", promo_price=Decimal("90000"))
    obj.plans.update(promo_ends_at=timezone.now() + timedelta(hours=5, minutes=30))

    for url in (reverse("home"), reverse("product", args=[obj.slug])):
        body = client.get(url).content.decode()
        assert "badge--time" in body, url
        assert "مانده" in body, url
        # Rendered by the server, so it is right with JavaScript off and never
        # ticks; the seconds travel beside it so the end does not depend on the
        # visitor's clock. The fixed end time stands beside the badge.
        assert re.search(r'data-left="(\d+)"', body), url
        assert "۵\u00a0ساعت و ۲۹\u00a0دقیقه" in body or "۵\u00a0ساعت و ۳۰\u00a0دقیقه" in body, url
        assert "<time datetime=" in body, url


def test_a_discount_without_a_deadline_shows_no_clock(client, category):
    """Most discounts here have no end date. A clock that counts down to
    nothing is the kind of invented urgency this shop does not use."""
    obj = make_product(category, "forever", promo_price=Decimal("90000"))
    obj.plans.update(promo_ends_at=None)

    body = client.get(reverse("product", args=[obj.slug])).content.decode()

    assert "badge--time" not in body
    assert "۹۰,۰۰۰" in body, "the discount itself is still on the page"


def test_a_deadline_that_has_passed_is_neither_a_price_nor_a_clock(client, category):
    """The window closing takes the promotional price with it, through the
    same `promo_window_open_at` the cart and the checkout use. Nothing here
    needs to remember to expire anything."""
    obj = make_product(category, "over", promo_price=Decimal("90000"))
    obj.plans.update(promo_ends_at=timezone.now() - timedelta(minutes=1))

    body = client.get(reverse("product", args=[obj.slug])).content.decode()

    assert "badge--time" not in body
    assert "۹۰,۰۰۰" not in body, "an expired promotion is not a price"
    assert "۲۰۰,۰۰۰" in body, "the sale price is what is charged"


def test_the_clock_counts_days_once_a_clock_would_be_absurd(client, category):
    obj = make_product(category, "long", promo_price=Decimal("90000"))
    obj.plans.update(promo_ends_at=timezone.now() + timedelta(days=17, hours=4, minutes=30))

    body = client.get(reverse("product", args=[obj.slug])).content.decode()

    assert "۱۷\u00a0روز و ۴\u00a0ساعت</span> مانده" in body
    assert "۴۱۲:" not in body, "a clock reading 412 hours is a number, not a deadline"


# --- Search ---------------------------------------------------------------------


def test_search_finds_sloppy_arabic_keyboard_input(client, product):
    body = client.get(reverse("search") + "?q=كلاود").content.decode()  # arabic kaf

    assert "Claude Pro" in body
    assert "۱ سرویس" in body


def test_search_miss_is_a_designed_empty_state(client, product):
    """It used to be a card saying «پیدا نشد» with a link home. It is the
    catalogue now: with ten products the shelf is a better answer than a
    sentence, and the heading takes the blame in the first person the way
    every Iranian shop surveyed does."""
    body = client.get(reverse("search") + "?q=xyzzy").content.decode()

    assert "پیدا نکردیم" in body
    assert product.name in body, "the shelf should be on the page"
    assert "پیدا نشد" not in body, "the passive puts the miss on the visitor"


def test_search_rate_limited(client, product):
    for _ in range(ratelimit.SEARCH_PER_IP.times):
        assert client.get(reverse("search") + "?q=a").status_code == 200

    response = client.get(reverse("search") + "?q=a")

    assert response.status_code == 429
    assert "جستجوها در یک دقیقه‌ی اخیر زیاد بود." in response.content.decode()


@pytest.mark.parametrize("query", ["خرید اکانت جمینای", "خرید اکانت جمینای پرو", "اکانت gemini"])
def test_the_words_around_a_product_name_do_not_sink_the_search(client, category, query):
    """«خرید اکانت جمینای پرو» is how people type it, and no product name holds
    «خرید» or «اکانت», so the word-by-word match found nothing and the page
    said the shop had no Gemini. The aliases are the seeded product's."""
    gemini = make_product(category, "gemini-advanced")
    gemini.name = "Gemini Advanced"
    gemini.aliases = "جمینای، جمنای، جیمینای، gemini، هوش مصنوعی گوگل"
    gemini.save()

    response = client.get(reverse("search"), {"q": query})

    # The results, not the page: a miss lists the whole catalogue, Gemini included.
    assert [p.slug for p in response.context["results"]] == ["gemini-advanced"]


def test_a_search_miss_names_the_query_and_offers_the_catalogue(client, product):
    body = client.get(reverse("search"), {"q": "xbox"}).content.decode()

    assert 'برای «<bdi lang="en">xbox</bdi>» محصولی پیدا نکردیم' in body
    assert "همه‌ی سرویس‌های ما در نرخ‌نامه‌ی زیر است." in body


def test_the_search_page_is_not_indexed(client, product):
    """A results page per query is an infinite set of thin pages to a crawler."""
    robots = '<meta name="robots" content="noindex">'

    assert robots in client.get(reverse("search"), {"q": "claude"}).content.decode()
    assert robots not in client.get(reverse("home")).content.decode()
    assert robots not in client.get(reverse("product", args=[product.slug])).content.decode()


# --- The buy box and the price component --------------------------------------


def price_components(body: str) -> list[str]:
    return PRICE_COMPONENT.findall(body)


def test_a_zero_price_reads_free_on_the_card_and_in_the_buy_box(client, category):
    """The rule was enforced in the `price_html` filter, which no page used:
    the card and the buy box printed «۰ تومان» through `_price.html`."""
    free = make_product(category, "free", sale_price=Decimal("0"))

    for url in (reverse("category", args=[category.slug]), reverse("product", args=[free.slug])):
        prices = price_components(client.get(url).content.decode())
        assert prices, url
        for markup in prices:
            assert '<span class="price__num">رایگان</span>' in markup, url
            assert "تومان" not in markup and "۰" not in markup, url

    # Beside a paid plan the card says «from» the cheapest, and nothing is from free.
    Plan.objects.create(product=free, title="۳ ماهه", cost_price=1, sale_price=Decimal(300000))
    listing = client.get(reverse("category", args=[category.slug])).content.decode()
    assert '<span class="price__num">رایگان</span>' in listing
    assert 'class="pcard__pre"' not in listing


def test_the_first_available_plan_is_selected(client, category):
    """With the first plan sold out nothing was checked, and the button still
    offered to add whatever that meant to the cart."""
    obj = make_product(category, "sold-first")
    obj.plans.update(is_available=False)
    second = Plan.objects.create(
        product=obj,
        title="۳ ماهه",
        cost_price=Decimal("1"),
        sale_price=Decimal("500000"),
        sort_order=5,
    )

    body = client.get(reverse("product", args=[obj.slug])).content.decode()

    checked = re.findall(r'value="(\d+)"[^>]*\schecked', body)
    assert checked == [str(second.id)]


def test_until_there_is_a_cart_the_order_goes_to_telegram(client, product):
    """A disabled cart button looked live and did nothing. Until S4b the
    invoice's one action is a working link to the support channel, the line
    under it says why, and the form has nothing to submit."""
    body = client.get(reverse("product", args=[product.slug])).content.decode()
    form = body.split('class="invoice__form"', 1)[1].split("</form>", 1)[0]

    assert 'class="invoice__form" method="post" action="#" data-cart-pending>' in body
    telegram = support.details()["telegram_url"]
    assert re.search(rf'<a class="[^"]*invoice__cta" href="{telegram}"', form)
    assert "فعلاً سفارش را در تلگرام می‌گیریم" in form
    assert 'type="submit"' not in form and "<button" not in form


def test_a_rate_sheet_row_opens_the_page_with_its_plan_chosen(client, product):
    """`?plan=` is honoured only for an available plan of this product;
    anything else falls back to the first available plan."""
    plans = list(product.plans.order_by("sort_order"))
    third, sold_out = plans[2], product.plans.get(is_available=False)
    other = make_product(product.category, "other").plans.get()
    url = reverse("product", args=[product.slug])

    def checked(query: str) -> list[str]:
        body = client.get(url + query).content.decode()
        return re.findall(r'value="(\d+)"[^>]*\schecked', body)

    assert checked(f"?plan={third.pk}") == [str(third.pk)]
    for junk in (f"?plan={sold_out.pk}", f"?plan={other.pk}", "?plan=x", ""):
        assert checked(junk) == [str(plans[0].pk)], junk


def test_the_invoice_has_a_server_total_for_every_plan_and_quantity(client, product):
    """The script picks a figure and computes none, so the server must have
    built one for every pair it can be asked for, now and after the discount."""
    gift = product.plans.get(title="۳ ماهه")
    gift.max_quantity = 3
    gift.save()

    body = client.get(reverse("product", args=[product.slug])).content.decode()
    data = json.loads(re.search(r'<script type="application/json">(.*?)</script>', body, re.S)[1])

    available = product.plans.filter(is_available=True)
    assert set(data) == {str(plan.pk) for plan in available}
    for plan in available:
        moments = {"now": None, "after": plan.promo_ends_at}
        for phase, at in moments.items():
            price = effective_price(plan, at=at) if at else effective_price(plan)
            figures = data[str(plan.pk)][phase]
            assert set(figures) == {str(n) for n in range(1, plan.max_quantity + 1)}
            for n, figure in figures.items():
                total = price * int(n)
                assert figure == {
                    "amount": format_toman(total, with_unit=False),
                    "words": toman_in_words(total),
                }
    discounted = data[str(product.plans.get(title="۱ ماهه").pk)]
    assert discounted["now"]["1"]["amount"] == "۱,۲۰۰,۰۰۰"
    assert discounted["after"]["1"]["amount"] == "۱,۴۵۰,۰۰۰", "positive control: the two differ"


def test_a_plan_title_on_the_rate_sheet_is_isolated(client, category):
    """«12 Months Family Plan» inside a Persian line reorders without bdi."""
    obj = make_product(category, "family")
    obj.plans.update(title="12 Months Family Plan")

    body = client.get(reverse("category", args=[category.slug])).content.decode()

    assert '"><bdi lang="en">12 Months Family Plan</bdi></a></span>' in body


def test_a_category_intro_is_restricted_markdown_never_markup(client, category, product):
    """Anyone who could edit a category could put a script on a page customers
    pay on. The intro goes through the restricted renderer, with no heading
    level free: it sits directly under the page's h1."""
    category.intro = "<script>alert(1)</script> **پررنگ**\n\n### تیتر"
    category.intro_html = "<b>the old column</b>"
    category.save()

    body = client.get(reverse("category", args=[category.slug])).content.decode()

    assert "<script>alert" not in body
    assert '&lt;<bdi lang="en">script</bdi>&gt;' in body
    assert "<strong>پررنگ</strong>" in body
    assert "<p>تیتر</p>" in body and "<h3>تیتر" not in body
    assert "the old column" not in body


def test_one_shared_end_prints_once_and_ends_that_differ_print_on_their_plans(client, category):
    obj = make_product(category, "ends", promo_price=Decimal("90000"))
    first = obj.plans.get()
    first.promo_ends_at = timezone.now() + timedelta(hours=3)
    first.save()
    second = Plan.objects.create(
        product=obj,
        title="۳ ماهه",
        cost_price=Decimal("1"),
        sale_price=Decimal("500000"),
        promo_price=Decimal("400000"),
        promo_ends_at=first.promo_ends_at,
        sort_order=1,
    )
    url = reverse("product", args=[obj.slug])

    shared = client.get(url).content.decode()
    assert shared.count('class="invoice__promo"') == 1 and 'class="plan__clock"' not in shared

    second.promo_ends_at += timedelta(days=2)
    second.save()
    apart = client.get(url).content.decode()
    assert 'class="invoice__promo"' not in apart and apart.count('class="plan__clock"') == 2


def test_a_discount_carries_the_price_it_falls_back_to(client, category):
    """When the clock reaches zero the page swaps in the price the server
    rendered for that moment, from the same `effective_price` the cart uses."""
    obj = make_product(category, "ending", promo_price=Decimal("90000"))
    plan = obj.plans.get()
    plan.promo_ends_at = timezone.now() + timedelta(hours=2)
    plan.save()

    body = client.get(reverse("product", args=[obj.slug])).content.decode()

    after = effective_price(plan, at=plan.promo_ends_at)
    assert after == plan.sale_price
    assert re.search(r'class="price[^"]*" data-left="\d+" data-after="۲۰۰,۰۰۰"', body)


def test_an_empty_query_does_not_spend_the_search_budget(client, category, product):
    """An empty box now redirects to the shelf rather than rendering a heading
    over nothing, but the budget rule is unchanged: pressing Enter on an empty
    field must never be what locks someone out of searching."""
    for _ in range(ratelimit.SEARCH_PER_IP.times + 5):
        assert client.get(reverse("search")).status_code == 302

    spent = client.get(reverse("search"), {"q": product.name})
    assert spent.status_code == 200, "a real query after many empty ones still works"

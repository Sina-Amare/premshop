"""Live suggestions under the header search: the endpoint, then the page in Chromium."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils.html import strip_tags
from playwright.sync_api import expect

from apps.catalog.models import Category, Plan, Product
from apps.catalog.pricing import effective_price
from apps.catalog.tests.test_admin import catalogue as catalogue
from apps.catalog.tests.test_admin import form_of
from apps.catalog.tests.test_admin import operator as operator
from apps.core import ratelimit
from apps.core.formatting import format_toman
from apps.core.tests.test_accessibility_browser import axe

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
WIDTHS = pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["390", "1440"])


@pytest.fixture
def category():
    return Category.objects.create(name="موسیقی", slug="music")


def make(category, name, slug, *, status=Product.Status.ACTIVE, aliases="", price="200000"):
    product = Product.objects.create(
        category=category,
        name=name,
        slug=slug,
        aliases=aliases,
        brand_colour="#1db954",
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=status,
    )
    Plan.objects.create(
        product=product, title="۱ ماهه", cost_price=Decimal("0"), sale_price=Decimal(price)
    )
    return product


def suggest(client, query):
    return client.get(reverse("search-suggest"), {"q": query})


def urls(response) -> list[str]:
    return [item["url"] for item in response.json()["results"]]


def said(html: str) -> str:
    """The row's text as a reader meets it, one space between the pieces."""
    return " ".join(strip_tags(html.replace("<", " <")).split())


# --- The endpoint ---------------------------------------------------------------


@pytest.mark.django_db
def test_only_public_products_are_suggested(client, category):
    make(category, "Spotify Premium", "spotify")
    make(category, "Spotify Family", "spotify-family", status=Product.Status.DRAFT)
    make(category, "Spotify Duo", "spotify-duo", status=Product.Status.UNAVAILABLE)

    assert urls(suggest(client, "spot")) == ["/p/spotify/"]


# The second is typed on an Arabic keyboard: its ي only matches once folded.
@pytest.mark.django_db
@pytest.mark.parametrize("query", ["چت", "چت جيپت"])
def test_a_persian_spelling_finds_the_latin_name(client, category, query):
    make(category, "ChatGPT Plus", "chatgpt-plus", aliases="چت جی‌پی‌تی، چت جیپت")
    make(category, "Spotify Premium", "spotify")

    assert urls(suggest(client, query)) == ["/p/chatgpt-plus/"]


@pytest.mark.django_db
def test_at_most_five_and_a_fixed_number_of_queries(
    client, category, django_assert_max_num_queries
):
    for i in range(7):
        make(category, f"Spotify {i}", f"spotify-{i}")

    # One to decide between the exact and the close pass, the products, their plans.
    with django_assert_max_num_queries(3):
        response = suggest(client, "spotify")

    assert response.status_code == 200
    assert len(response.json()["results"]) == 5


@pytest.mark.django_db
def test_the_row_shows_the_mark_the_name_the_department_and_the_effective_price(client, category):
    product = make(category, "Spotify Premium", "spotify", price="390000")
    cheapest = product.plans.get()
    cheapest.promo_price = Decimal("312000")
    cheapest.save()
    Plan.objects.create(
        product=product, title="۱۲ ماهه", cost_price=Decimal("0"), sale_price=Decimal("3000000")
    )
    Plan.objects.create(
        product=product,
        title="قدیمی",
        cost_price=Decimal("0"),
        sale_price=Decimal("1000"),
        is_available=False,
    )

    [item] = suggest(client, "spot").json()["results"]
    html = item["html"]

    assert effective_price(cheapest) == Decimal("312000"), "positive control: the offer is live"
    assert f"از {format_toman(effective_price(cheapest))}" in said(html)
    assert format_toman(cheapest.sale_price, with_unit=False) not in html, "the list price"
    assert "۱,۰۰۰" not in html, "a plan that cannot be bought sets no starting price"
    assert "<bdi>Spotify Premium</bdi>" in html
    assert "موسیقی" in said(html)
    assert 'class="pcard__art"' in html and "--brand: #1db954" in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    "price, available, shown",
    [("200000", True, "۲۰۰,۰۰۰ تومان"), ("0", True, "رایگان"), ("200000", False, "ناموجود")],
)
def test_one_plan_is_worded_as_the_card_words_it(client, category, price, available, shown):
    product = make(category, "Spotify Premium", "spotify", price=price)
    product.plans.update(is_available=available)

    [item] = suggest(client, "spot").json()["results"]

    text = said(item["html"])
    assert text.endswith(shown)
    assert "از" not in text, "«از» claims there is more than one price"
    if shown == "رایگان":
        assert "تومان" not in text


@pytest.mark.django_db
def test_an_operator_written_name_is_escaped_before_the_page_inserts_it(client, category):
    make(category, "Spotify <img src=x onerror=alert(1)>", "spotify")

    [item] = suggest(client, "spot").json()["results"]

    assert "<img src=x" not in item["html"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in item["html"]


@pytest.mark.django_db
def test_the_list_spends_a_budget_of_its_own(client, category):
    """A pause in typing is a request, far more of them than a person submits,
    so typing must not be what locks the buyer out of the results page."""
    make(category, "Spotify Premium", "spotify")
    assert (
        ratelimit.SEARCH_SUGGEST_PER_IP.times > ratelimit.SEARCH_PER_IP.times
    ), "or this proves nothing"
    for _ in range(ratelimit.SEARCH_SUGGEST_PER_IP.times):
        assert suggest(client, "spot").status_code == 200

    limited = suggest(client, "spot")

    assert limited.status_code == 429
    assert limited.json() == {"results": []}
    assert client.get(reverse("search"), {"q": "spot"}).status_code == 200, "the page's budget"


@pytest.mark.django_db
def test_once_the_results_page_is_limited_the_list_is_too(client, category):
    """Read, not spent: the list is never a way round the page's limit."""
    make(category, "Spotify Premium", "spotify")
    for _ in range(ratelimit.SEARCH_PER_IP.times):
        ratelimit.hit(ratelimit.SEARCH_PER_IP, "127.0.0.1")

    limited = suggest(client, "spot")

    assert limited.status_code == 429
    assert limited.json() == {"results": []}


@pytest.mark.django_db
def test_the_admin_preview_leaves_the_list_out(client, operator, catalogue):
    """The preview cancels every link and submit, and a row is neither, so with
    the list a click would take the preview to the live product page."""
    product = catalogue["draft"]
    data = form_of(client, reverse("admin:catalog_product_change", args=[product.pk]))

    preview = client.post(reverse("admin:catalog_product_preview", args=[product.pk]), data)

    assert "js/snapshot-frame.js" in preview.content.decode(), "positive control: the preview"
    assert "js/search.js" not in preview.content.decode()
    assert "js/search.js" in client.get(reverse("home")).content.decode()


# «ـس» is a tatweel and one letter: the tatweel folds away. Filler words are
# dropped before searching, so «اکانت س» searches for one letter.
@pytest.mark.django_db
@pytest.mark.parametrize("query", ["", "s", "  s  ", "ـس", "اکانت س", "خرید"])
def test_a_short_query_suggests_nothing_and_costs_nothing(
    client, category, query, django_assert_num_queries
):
    make(category, "Spotify Premium", "spotify")
    make(category, "سریال", "serial")

    with django_assert_num_queries(0):
        response = suggest(client, query)

    assert response.status_code == 200
    assert response.json() == {"results": []}
    for limit in (ratelimit.SEARCH_PER_IP, ratelimit.SEARCH_SUGGEST_PER_IP):
        assert ratelimit.remaining(limit, "127.0.0.1") == limit.times, limit.name


# --- The page, in Chromium --------------------------------------------------------


@pytest.fixture
def shelf(category):
    make(category, "Spotify Premium", "spotify", price="390000")
    make(category, "ChatGPT Plus", "chatgpt-plus")


def open_home(browser, live_server, viewport):
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    page.goto(f"{live_server.url}/")
    return context, page, page.get_by_role("combobox", name="جستجو در محصولات")


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@WIDTHS
def test_typing_suggests_and_the_keyboard_opens_the_product(browser, live_server, shelf, viewport):
    context, page, field = open_home(browser, live_server, viewport)
    try:
        field.press_sequentially("spot")
        listbox = page.get_by_role("listbox", name="نتایج جستجو")
        option = listbox.get_by_role("option")

        expect(listbox).to_be_visible()
        expect(field).to_have_attribute("aria-expanded", "true")
        expect(field).to_have_attribute("aria-controls", "search-suggest")
        expect(option).to_have_count(1)
        expect(option).to_contain_text("Spotify Premium")
        expect(option).to_contain_text("۳۹۰,۰۰۰")

        form, box = page.locator(".v-search").bounding_box(), listbox.bounding_box()
        assert box["y"] >= form["y"] + form["height"], (form, box)
        assert abs(box["x"] - form["x"]) <= 1 and abs(box["width"] - form["width"]) <= 1
        assert page.evaluate("document.documentElement.scrollWidth") <= viewport["width"]

        field.press("ArrowDown")
        expect(option).to_have_attribute("aria-selected", "true")
        expect(field).to_have_attribute("aria-activedescendant", option.get_attribute("id"))
        # The page scans only ever meet the list hidden.
        assert not axe(page)

        field.press("Enter")
        page.wait_for_url(f"{live_server.url}/p/spotify/")
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@WIDTHS
def test_escape_and_a_click_outside_close_the_list(browser, live_server, shelf, viewport):
    context, page, field = open_home(browser, live_server, viewport)
    listbox = page.locator("#search-suggest")
    try:
        field.press_sequentially("spot")
        expect(listbox).to_be_visible()
        field.press("Escape")
        expect(listbox).to_be_hidden()
        expect(field).to_have_attribute("aria-expanded", "false")
        expect(field).to_have_value("spot")

        field.press("ArrowDown")
        expect(listbox).to_be_visible()
        page.locator(".v-hero__lead").click()
        expect(listbox).to_be_hidden()
        expect(field).to_have_attribute("aria-expanded", "false")
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_enter_with_nothing_highlighted_goes_to_the_results_page(browser, live_server, shelf):
    context, page, field = open_home(browser, live_server, DESKTOP)
    try:
        field.press_sequentially("spot")
        expect(page.locator("#search-suggest")).to_be_visible()
        field.press("Enter")
        page.wait_for_url(f"{live_server.url}/search/?q=spot")
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_the_field_waits_for_two_letters_and_a_pause(browser, live_server, shelf):
    """Every request spends a per-IP budget, so a word typed quickly is one
    request, not one per letter."""
    context, page, field = open_home(browser, live_server, DESKTOP)
    sent = []
    page.on("request", lambda r: sent.append(r.url) if "/search/suggest/" in r.url else None)
    try:
        field.press_sequentially("s")
        page.wait_for_timeout(400)
        assert sent == []

        field.press_sequentially("potify")
        expect(page.locator("#search-suggest")).to_be_visible()
        page.wait_for_timeout(400)
        assert len(sent) == 1 and sent[0].endswith("?q=spotify"), sent
    finally:
        context.close()


# Each way of leaving comes inside the pause after «i», before its request.
@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("leave", ["Escape", "Shift+Tab", "tap"])
def test_closing_inside_the_pause_keeps_the_list_closed(browser, live_server, shelf, leave):
    """Otherwise the answer reopens the list after the buyer has moved on, where
    with focus elsewhere only a pointer can close it."""
    context, page, field = open_home(browser, live_server, PHONE)
    listbox = page.locator("#search-suggest")
    try:
        field.press_sequentially("spot")
        expect(listbox).to_be_visible()
        field.press_sequentially("i")
        if leave == "tap":
            page.locator(".v-hero__lead").click()
        else:
            field.press(leave)
        page.wait_for_timeout(500)

        expect(listbox).to_be_hidden()
        expect(field).to_have_attribute("aria-expanded", "false")
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_an_answer_that_lands_after_escape_does_not_reopen_the_list(browser, live_server, shelf):
    context, page, field = open_home(browser, live_server, DESKTOP)
    listbox = page.locator("#search-suggest")
    held = []
    page.route(lambda url: url.endswith("?q=spoti"), lambda route: held.append(route))
    try:
        field.press_sequentially("spot")
        expect(listbox).to_be_visible()
        with page.expect_request(lambda r: r.url.endswith("?q=spoti")):
            field.press_sequentially("i")
        field.press("Escape")
        held[0].continue_()
        page.wait_for_timeout(500)

        expect(listbox).to_be_hidden()
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_without_javascript_the_search_is_as_it_was(browser, live_server, shelf):
    context = browser.new_context(viewport=PHONE, java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        field = page.get_by_role("searchbox", name="جستجو در محصولات")
        field.fill("spot")
        page.wait_for_timeout(400)
        expect(page.locator("#search-suggest")).to_be_hidden()
        assert field.get_attribute("role") is None

        field.press("Enter")
        page.wait_for_url(f"{live_server.url}/search/?q=spot")
    finally:
        context.close()

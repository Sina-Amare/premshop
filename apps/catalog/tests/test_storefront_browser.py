"""The storefront in a real Chromium: the countdown, and the phone's width.

Two things the test client cannot see. The discount clock is JavaScript, so
nothing below the template is proof that it runs. And horizontal overflow is
a layout result, not markup: the deadline badge shipped as one nowrap element
too many in the buy box, which pushed the whole product page sideways at
390px, and no assertion over HTML would have noticed.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product, ProductSection
from apps.catalog.pricing import effective_price
from apps.core.formatting import format_toman
from apps.core.tests.test_accessibility_browser import STYLED

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

PHONE = {"width": 390, "height": 844}


@pytest.fixture
def discounted():
    """One product, one plan, a discount ending in a little under two hours."""
    category = Category.objects.create(name="سرگرمی", slug="fun")
    product = Product.objects.create(
        category=category,
        name="Netflix",
        slug="netflix",
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(
        product=product,
        title="۱ ماهه",
        cost_price=Decimal("100000"),
        sale_price=Decimal("390000"),
        promo_price=Decimal("312000"),
        promo_ends_at=timezone.now() + timedelta(hours=1, minutes=55),
    )
    return product


def widest_overflow(page) -> list[dict]:
    return page.evaluate("""() => {
        const w = document.documentElement.clientWidth, over = [];
        document.querySelectorAll('body *').forEach(e => {
          const b = e.getBoundingClientRect();
          if (b.width > w + 1) over.push({cls: String(e.className).slice(0, 40), w: Math.round(b.width)});
        });
        return over;
      }""")


@pytest.mark.parametrize("path", ["/", "/c/fun/", "/p/netflix/"])
def test_no_page_scrolls_sideways_on_a_phone(browser, live_server, discounted, path):
    context = browser.new_context(viewport=PHONE, device_scale_factor=1)
    page = context.new_page()
    page.goto(f"{live_server.url}{path}")
    try:
        assert not widest_overflow(page), path
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"], path
    finally:
        context.close()


def test_the_discount_clock_counts_down(browser, live_server, discounted):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/p/netflix/")
        badge = page.locator(".deadline").first
        first = badge.text_content()
        assert "مانده" in first
        assert "۰۱:5" in first.replace("۵", "5"), first

        page.wait_for_timeout(2100)
        assert badge.text_content() != first, "the clock is painted once and never moves"
    finally:
        context.close()


TIME_TEXT = "rgb(146, 64, 14)"  # --color-time-text: amber means time pressure


def test_another_plans_expiry_removes_its_offer_without_changing_the_starting_price(
    browser, live_server, discounted
):
    discounted.plans.create(title="شروع", cost_price=1, sale_price=200000, sort_order=1)
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}/c/fun/")
        card = page.locator(".pcard").first
        assert card.locator(".pcard__price .price__num").text_content() == "۲۰۰,۰۰۰"
        assert card.locator(".pcard__offer:visible").count() == 1
        page.clock.fast_forward("02:00:00")
        assert card.locator(".pcard__offer:visible").count() == 0
        assert card.locator(".pcard__promotion:visible").count() == 0
        assert card.locator(".pcard__price .price__num").text_content() == "۲۰۰,۰۰۰"
    finally:
        context.close()


def test_successive_expiries_keep_the_offer_and_starting_plan_accurate(
    browser, live_server, discounted
):
    discounted.plans.create(
        title="۳ ماهه",
        cost_price=1,
        sale_price=450000,
        promo_price=330000,
        promo_ends_at=timezone.now() + timedelta(hours=4),
        sort_order=1,
    )
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}/c/fun/")
        card = page.locator(".pcard").first
        assert "۳ ماهه" in card.locator(".pcard__promotion:visible").text_content()
        assert card.locator(".pcard__price .price__num").text_content() == "۳۱۲,۰۰۰"
        page.clock.fast_forward("02:00:00")
        assert card.locator(".pcard__price .price__num").text_content() == "۳۳۰,۰۰۰"
        assert card.locator(".pcard__from bdi").text_content() == "۳ ماهه"
        page.clock.fast_forward("03:00:00")
        assert card.locator(".pcard__price .price__num").text_content() == "۳۹۰,۰۰۰"
        assert card.locator(".pcard__from bdi").text_content() == "۱ ماهه"
        assert card.locator(".pcard__offer:visible").count() == 0
    finally:
        context.close()


def test_expiring_the_highest_offer_reveals_another_active_offer(browser, live_server, discounted):
    discounted.plans.create(
        title="۳ ماهه",
        cost_price=1,
        sale_price=1000000,
        promo_price=900000,
        sort_order=1,
    )
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}/c/fun/")
        card = page.locator(".pcard").first
        assert card.locator(".pcard__offer:visible").text_content().strip() == "۲۰٪ تخفیف"
        page.clock.fast_forward("02:00:00")
        assert card.locator(".pcard__offer:visible").text_content().strip() == "۱۰٪ تخفیف"
        assert "۳ ماهه" in card.locator(".pcard__promotion:visible").text_content()
    finally:
        context.close()


def test_selected_plan_summary_tracks_selection_and_promotion_expiry(
    browser, live_server, discounted
):
    other = discounted.plans.create(
        title="۳ ماهه",
        cost_price=1,
        sale_price=900000,
        max_quantity=3,
        sort_order=1,
    )
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}/p/netflix/")
        selected = page.locator("[data-selected-price]")
        assert selected.locator(".price__num").text_content() == "۳۱۲,۰۰۰"
        page.locator(f'input[name="plan"][value="{other.pk}"]').check()
        assert page.locator("[data-selected-title]").text_content() == "۳ ماهه"
        assert selected.locator(".price__num").text_content() == "۹۰۰,۰۰۰"
        assert page.locator("#qty-row").is_visible()
        first = discounted.plans.get(title="۱ ماهه")
        page.locator(f'input[name="plan"][value="{first.pk}"]').check()
        assert page.locator("#qty-row").is_hidden()
        page.clock.fast_forward("02:00:00")
        assert selected.locator(".price__num").text_content() == "۳۹۰,۰۰۰"
        assert page.locator(".plan__promotion [data-offer-badge]:visible").count() == 0
        assert page.locator(".buybox__cta").is_disabled()
    finally:
        context.close()


@pytest.mark.parametrize(("path", "scope"), [("/p/netflix/", ".plan"), ("/c/fun/", ".pcard")])
def test_at_zero_the_regular_price_returns_and_the_badge_stops_being_amber(
    browser, live_server, discounted, path, scope
):
    """The badge said the discount was over while the discounted price stayed
    on screen in amber's company: `.badge--time` came after `.deadline--done`
    in the stylesheet and won, and the script never touched the price."""
    plan = discounted.plans.get()
    after = format_toman(effective_price(plan, at=plan.promo_ends_at), with_unit=False)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}{path}")
        assert page.evaluate(STYLED), "unstyled, nothing is amber and the colour check is empty"
        box = page.locator(scope).first
        badge = box.locator(".deadline")
        assert box.locator(".price__num").text_content() == "۳۱۲,۰۰۰"
        assert badge.evaluate("e => getComputedStyle(e).color") == TIME_TEXT, "positive control"

        page.clock.fast_forward("02:00:00")

        assert box.locator(".price__num").text_content() == after == "۳۹۰,۰۰۰"
        assert box.locator(".price__was").count() == 0, "a struck price beside itself"
        assert box.locator(".price__now").count() == 0, "«قیمت با تخفیف» for a regular price"
        assert badge.evaluate("e => getComputedStyle(e).color") != TIME_TEXT
        assert badge.text_content() == "تخفیف تمام شد"
    finally:
        context.close()


def test_at_zero_a_card_leads_with_the_plan_that_is_cheapest_then(browser, live_server, discounted):
    """The card swapped in its own plan's regular price, 390,000, while
    another plan cost 350,000, and kept saying so until a reload."""
    discounted.plans.create(
        title="۱ ماهه، بدون تخفیف",
        cost_price=Decimal("100000"),
        sale_price=Decimal("350000"),
        sort_order=1,
    )
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}/c/fun/")
        card = page.locator(".pcard").first
        assert card.locator(".price__num").text_content() == "۳۱۲,۰۰۰", "positive control"
        assert card.locator(".pcard__from bdi").text_content() == "۱ ماهه"

        page.clock.fast_forward("02:00:00")

        assert card.locator(".price__num").text_content() == "۳۵۰,۰۰۰"
        assert card.locator(".pcard__from bdi").text_content() == "۱ ماهه، بدون تخفیف"
    finally:
        context.close()


def test_at_zero_a_plans_title_keeps_its_line_on_a_phone(browser, live_server, discounted):
    """With the struck price gone, the wider «تخفیف تمام شد» fit on the first
    line and squeezed the title to its narrowest: «۱» over «ماهه»."""
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}/p/netflix/")
        assert page.evaluate(STYLED)
        title = page.locator(".plan__title bdi").first
        assert title.evaluate(LINES) == 1, "positive control"

        page.clock.fast_forward("02:00:00")

        assert page.locator(".plan .deadline").first.text_content() == "تخفیف تمام شد"
        assert title.evaluate(LINES) == 1
    finally:
        context.close()


# The lines an inline element is set on. Its rectangles are one per bidi run,
# not one per line, so they are counted by their tops.
LINES = "e => new Set([...e.getClientRects()].map(r => Math.round(r.top))).size"


def test_a_ticking_clock_is_not_part_of_any_name(browser, live_server, discounted):
    """Under a day the badge counts seconds, inside the plan's label and the
    card's link, so both names changed every second and a screen reader on
    a plan heard its whole label again each time."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        for path, scope in (("/p/netflix/", ".plans"), ("/c/fun/", ".pcard")):
            page.goto(f"{live_server.url}{path}")
            box = page.locator(scope).first
            heard, shown = box.aria_snapshot(), box.locator(".deadline").text_content()
            page.wait_for_timeout(2100)
            assert box.locator(".deadline").text_content() != shown, "positive control: it ticks"
            assert box.aria_snapshot() == heard, path
            # What is heard instead: the end once, and which price is which.
            assert "تخفیف تا" in heard, heard
            if scope == ".plans":
                assert "قیمت قبلی" in heard and "قیمت با تخفیف" in heard, heard
            else:
                assert "پلن ۱ ماهه" in heard and "۲۰٪ تخفیف" in heard, heard
    finally:
        context.close()


def test_the_skip_link_appears_on_focus_and_lands_in_the_content(browser, live_server, discounted):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        page.keyboard.press("Tab")
        link = page.locator('a[href="#main"]')
        assert link.evaluate("e => e === document.activeElement")
        box = link.bounding_box()
        assert box and box["width"] > 40 and box["height"] > 20 and box["y"] >= 0, box

        page.keyboard.press("Enter")
        assert page.evaluate("document.activeElement.id") == "main"
    finally:
        context.close()


def header_bottom(page) -> float:
    return page.evaluate("document.querySelector('.hdr').getBoundingClientRect().bottom")


@pytest.mark.parametrize("width", [390, 1280])
def test_an_in_page_jump_lands_below_the_header(browser, live_server, discounted, width):
    context = browser.new_context(viewport={"width": width, "height": 800})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        assert page.evaluate(STYLED)
        page.click('a[href="#products"]')
        page.wait_for_function("location.hash === '#products'")
        page.wait_for_timeout(300)
        # Unstyled, the header scrolls away and anything clears it.
        assert header_bottom(page) > 0, "positive control: the header is pinned"
        top = page.evaluate("document.getElementById('products').getBoundingClientRect().top")
        assert top >= header_bottom(page) - 1, (width, top, header_bottom(page))
    finally:
        context.close()


@pytest.mark.parametrize("width", [320, 390, 640, 760])
def test_the_phone_bar_gives_search_its_own_row(browser, live_server, discounted, width):
    """From 521px to 760px a later rule put the search back beside the brand,
    flush against the top of a bar 47px shorter than every in-page jump
    allowed for. Narrower, the account button touched the top edge."""
    context = browser.new_context(viewport={"width": width, "height": 800})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        bar, account, search = (
            page.locator(selector).bounding_box()
            for selector in (".hdr__main", ".hdr__account", ".hdr__search")
        )
        assert search["y"] >= account["y"] + account["height"], (account, search)
        assert account["y"] - bar["y"] >= 8, (bar, account)
        pinned = header_bottom(page) - bar["y"]
        allowed = page.evaluate(
            "parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--hdr-h'))"
        )
        assert pinned <= allowed <= pinned + 1, (pinned, allowed)
    finally:
        context.close()


def test_the_sticky_buy_box_stays_below_the_header(browser, live_server, discounted):
    """It stuck 24px from the top of the viewport, which is under a 69px bar."""
    for i in range(14):
        discounted.specs.create(title=f"ردیف {i}", value="مقدار", sort_order=i)
    context = browser.new_context(viewport={"width": 1280, "height": 700})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/p/netflix/")
        assert page.evaluate(STYLED)
        page.mouse.wheel(0, 500)
        page.wait_for_function("window.scrollY > 400")
        # Unstyled, neither the box nor the header sticks, and the box's top is
        # above a header that has scrolled off.
        box = page.locator(".pdp__buy")
        assert box.evaluate("e => getComputedStyle(e).position") == "sticky", "positive control"
        assert header_bottom(page) > 0, "positive control: the header is pinned"
        top = box.evaluate("e => e.getBoundingClientRect().top")
        assert top >= header_bottom(page), (top, header_bottom(page))
    finally:
        context.close()


COMPARISON = """| | Go | Plus | Pro |
|---|---|---|---|
| قیمت در OpenAI | ۸ دلار در ماه | ۲۰ دلار در ماه | از ۱۰۰ دلار در ماه |
| Codex | محدود | ترمینال، ویرایشگر کد، اپ دسکتاپ، وب، iOS و Android (پیش‌نمایش) | جلسه‌های طولانی‌تر از Plus، فعلاً بدون سقف ۵ ساعته |
"""


def test_a_wide_table_scrolls_inside_its_section_on_a_small_phone(browser, live_server, discounted):
    """One column of `1fr` is as wide as its widest table, so at 320px the
    whole page scrolled sideways while the table's own box had nothing to do."""
    ProductSection.objects.create(product=discounted, kind="comparison", body=COMPARISON)
    context = browser.new_context(viewport={"width": 320, "height": 700}, device_scale_factor=1)
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/p/netflix/")
        assert page.evaluate(STYLED)
        assert page.evaluate("document.documentElement.scrollWidth") <= 320
        box = page.locator(".rich__scroll")
        assert box.evaluate("e => e.scrollWidth > e.clientWidth"), "the table fits: nothing tested"
    finally:
        context.close()

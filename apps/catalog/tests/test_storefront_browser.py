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
    # Tile art is drawn twelve periods wide and clipped by its wall; if the wall stopped
    # clipping it, the scroll width checked beside this would say so.
    return page.evaluate("""() => {
        const w = document.documentElement.clientWidth, over = [];
        document.querySelectorAll('body *').forEach(e => {
          if (e.parentElement.closest('.wall')) return;
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


def test_the_deadline_never_ticks_and_is_written_again_on_return(browser, live_server, discounted):
    """The badge counted seconds: moving text nobody could stop (WCAG 2.2.2).
    The server writes hours and minutes beside the fixed end time, and the
    script writes them again only when the reader comes back to the tab."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}/p/netflix/")
        left = page.locator(".invoice__promo .countdown__left")
        first = left.text_content()
        assert first in ("۱\u00a0ساعت و ۵۴\u00a0دقیقه", "۱\u00a0ساعت و ۵۵\u00a0دقیقه"), first
        assert page.locator(".invoice__promo time[datetime]").count() == 1, "the end beside it"

        page.clock.fast_forward("01:00:00")
        assert left.text_content() == first, "the text moved on its own"

        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        written = left.text_content()
        assert written in ("۵۴\u00a0دقیقه", "۵۵\u00a0دقیقه"), "positive control: it can write"
    finally:
        context.close()


TIME_TEXT = "rgb(146, 64, 14)"  # --color-time-text: amber means time pressure
# The line a deadline stands on, which says the discount is over once the badge goes.
DEADLINE_LINE = ":is(.invoice__promo, .plan__clock, .sheet__note, .rs__clock):has(.countdown)"


@pytest.mark.parametrize(
    ("path", "row"), [("/p/netflix/", "#invoice .plan"), ("/c/fun/", "tr.rs__plan")]
)
def test_at_zero_the_regular_price_returns_and_the_line_says_the_discount_is_over(
    browser, live_server, discounted, path, row
):
    """The badge said the discount was over while the discounted price stayed
    on screen in amber's company, and the script never touched the price."""
    plan = discounted.plans.get()
    after = format_toman(effective_price(plan, at=plan.promo_ends_at), with_unit=False)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}{path}")
        assert page.evaluate(STYLED), "unstyled, nothing is amber and the colour check is empty"
        box = page.locator(row).first
        badge = page.locator(".countdown").first
        line = page.locator(DEADLINE_LINE).first.element_handle()
        assert box.locator(".price__num").text_content() == "۳۱۲,۰۰۰"
        assert badge.evaluate("e => getComputedStyle(e).color") == TIME_TEXT, "positive control"

        page.clock.fast_forward("02:00:00")

        assert box.locator(".price__num").text_content() == after == "۳۹۰,۰۰۰"
        assert box.locator(".price__was").count() == 0, "a struck price beside itself"
        assert box.locator(".price__now-k").count() == 0, "«قیمت با تخفیف» for a regular price"
        assert page.locator(".countdown:visible").count() == 0, "a clock past its end"
        assert "تمام شد" in line.text_content()
        if path.startswith("/p/"):
            assert page.locator(".invoice__amount").text_content() == "۳۹۰,۰۰۰"
            assert page.locator(".invoice__words-text").text_content() == "سیصد و نود هزار تومان"
    finally:
        context.close()


def test_at_zero_each_rate_sheet_row_keeps_its_own_plan(browser, live_server, discounted):
    """A card swapped in its own plan's regular price, 390,000, while another
    plan cost 350,000. Each plan has its own row now, and at zero only the
    discounted row changes, to the figure the server built for it."""
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
        rows = page.locator("tr.rs__plan")

        def prices() -> list[str | None]:
            return [rows.nth(i).locator(".price__num").text_content() for i in range(2)]

        assert rows.count() == 2 and prices() == ["۳۱۲,۰۰۰", "۳۵۰,۰۰۰"], "positive control"

        page.clock.fast_forward("02:00:00")

        assert prices() == ["۳۹۰,۰۰۰", "۳۵۰,۰۰۰"]
        assert "۳۹۰,۰۰۰" in rows.first.locator(".rs__plan-link").get_attribute("aria-label")
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

        assert page.locator(".invoice__promo").text_content() == "تخفیف تمام شد."
        assert title.evaluate(LINES) == 1
    finally:
        context.close()


# The lines an inline element is set on. Its rectangles are one per bidi run,
# not one per line, so they are counted by their tops.
LINES = "e => new Set([...e.getClientRects()].map(r => Math.round(r.top))).size"


def test_no_name_changes_while_the_page_is_open(browser, live_server, discounted):
    """Under a day the badge counted seconds inside the plan's label and the
    card's link, so both names changed every second and a screen reader on a
    plan heard its whole label again each time. Nothing ticks now."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        for path, scope in (("/p/netflix/", "#invoice"), ("/c/fun/", ".sheet:has(.rs)")):
            page.goto(f"{live_server.url}{path}")
            box = page.locator(scope).first
            heard = box.aria_snapshot()
            page.wait_for_timeout(2100)
            assert box.aria_snapshot() == heard, path
            # What is heard: the end, and which price is which.
            assert "تخفیف" in heard and "مانده" in heard, heard
            assert "قیمت قبل" in heard and "قیمت با تخفیف" in heard, heard
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
    return page.evaluate("document.querySelector('.site-header').getBoundingClientRect().bottom")


@pytest.mark.parametrize("width", [390, 1280])
def test_an_in_page_jump_lands_below_the_header(browser, live_server, discounted, width):
    context = browser.new_context(viewport={"width": width, "height": 800})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        assert page.evaluate(STYLED)
        # «همه‌ی سرویس‌ها» in the category row, which on the home page jumps to its rate sheet.
        page.click('.catnav a[href="/#rates"]')
        page.wait_for_function("location.hash === '#rates'")
        page.wait_for_timeout(300)
        top = page.evaluate("document.getElementById('rates').getBoundingClientRect().top")
        if width < 761:
            # The header sticks from 761px; below that the jump only has to land in view.
            assert top >= 0, (width, top)
            return
        # Unstyled, the header scrolls away and anything clears it.
        assert header_bottom(page) > 0, "positive control: the header is pinned"
        assert top >= header_bottom(page) - 1, (width, top, header_bottom(page))
    finally:
        context.close()


@pytest.mark.parametrize("width", [320, 390, 640, 760])
def test_the_phone_bar_gives_search_its_own_row(browser, live_server, discounted, width):
    """From 521px to 760px a later rule put the search back beside the brand.
    Narrower, the account button touched the top edge. The header links have
    no edge of their own now, so it is their icons that must clear it."""
    context = browser.new_context(viewport={"width": width, "height": 800})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        bar, links, icon, search = (
            page.locator(selector).first.bounding_box()
            for selector in (".site-header__in", ".hlinks", ".hlinks svg", ".search")
        )
        assert search["y"] >= links["y"] + links["height"], (links, search)
        assert icon["y"] - bar["y"] >= 8, (bar, icon)
    finally:
        context.close()


def test_the_sticky_invoice_keeps_its_button_below_the_header(browser, live_server, discounted):
    """It stuck 24px from the top of the viewport, which is under a 69px bar.
    An invoice taller than the room under the header anchors by its bottom, so
    the total and the button stay in view; a focused plan sticks it by its top."""
    for i in range(14):
        discounted.specs.create(title=f"ردیف {i}", value="مقدار", sort_order=i)
    context = browser.new_context(viewport={"width": 1280, "height": 700})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/p/netflix/")
        assert page.evaluate(STYLED)
        page.mouse.wheel(0, 500)
        page.wait_for_function("window.scrollY > 400")
        # Unstyled, neither the invoice nor the header sticks.
        box = page.locator("#invoice")
        assert box.evaluate("e => getComputedStyle(e).position") == "sticky", "positive control"
        assert header_bottom(page) > 0, "positive control: the header is pinned"
        for part in (".invoice__total", ".invoice__cta"):
            r = page.locator(part).bounding_box()
            assert header_bottom(page) <= r["y"] and r["y"] + r["height"] <= 700, (part, r)

        page.focus("#invoice input[name=plan]")
        page.wait_for_timeout(100)
        card = page.locator("#invoice .plan__body").first.bounding_box()
        assert card["y"] >= header_bottom(page), (card, header_bottom(page))

        page.set_viewport_size({"width": 1280, "height": 1000})
        page.locator("body").click(position={"x": 5, "y": 400})
        page.wait_for_timeout(100)
        top = box.evaluate("e => e.getBoundingClientRect().top")
        assert top >= header_bottom(page), ("room for all of it, yet its top is under", top)
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
        box = page.locator(".sec--comparison .table-scroll")
        assert box.evaluate("e => e.scrollWidth > e.clientWidth"), "the table fits: nothing tested"
    finally:
        context.close()

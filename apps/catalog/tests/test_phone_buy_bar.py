"""On a phone the buy box's button follows the reader down the product page."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from apps.cart.models import CartItem
from apps.catalog.models import Category, Plan, Product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
SHOTS = Path("screenshots/phone-buy-bar")


@pytest.fixture
def product():
    category = Category.objects.create(name="موسیقی", slug="music")
    product = Product.objects.create(
        category=category,
        name="Spotify Premium",
        slug="spotify",
        status=Product.Status.ACTIVE,
        short_description="پخش بدون تبلیغ و دانلود آهنگ‌ها برای گوش دادن بدون اینترنت.",
    )
    Plan.objects.create(product=product, title="یک ماهه", cost_price=1, sale_price=200000)
    Plan.objects.create(
        product=product,
        title="سه ماهه",
        cost_price=1,
        sale_price=600000,
        promo_price=500000,
        max_quantity=3,
        sort_order=1,
    )
    # A neighbour puts a rail between the buy box and the footer, so a wide screen
    # can scroll the box out of sight too.
    other = Product.objects.create(
        category=category, name="Tidal HiFi", slug="tidal", status=Product.Status.ACTIVE
    )
    Plan.objects.create(product=other, title="یک ماهه", cost_price=1, sale_price=300000)
    return product


def settle(page):
    """Two frames and a beat, so an observer that would show the bar has reported."""
    page.evaluate(
        "() => new Promise(done => requestAnimationFrame(() =>"
        " requestAnimationFrame(() => setTimeout(done, 80))))"
    )


def scroll_to(page, top):
    page.evaluate("top => scrollTo({top, behavior: 'instant'})", top)


def leave_the_buy_box(page):
    page.evaluate(
        "() => scrollTo({top: document.querySelector('.buybox__cta').getBoundingClientRect()"
        ".bottom + scrollY + 40, behavior: 'instant'})"
    )


def raise_cta_to_header_edge(page, rise):
    """Scrolls so the box's button centre sits `rise` pixels above the header's lower edge."""
    page.evaluate(
        "rise => { const box = document.querySelector('.buybox__cta').getBoundingClientRect();"
        " const edge = document.querySelector('.v-header').offsetHeight;"
        " scrollTo({top: scrollY + box.top + box.height / 2 - edge + rise, behavior: 'instant'}); }",
        rise,
    )


def wait_until_on_top(page, selector, over):
    """Waits until a tap at the centre of `selector` would land on `over`."""
    page.wait_for_function(
        "([selector, over]) => { const box = document.querySelector(selector).getBoundingClientRect();"
        " return document.querySelector(over).contains("
        "document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)); }",
        arg=[selector, over],
        timeout=5000,
    )


def cta_out_of_sight(page):
    return page.evaluate(
        "() => { const box = document.querySelector('.buybox__cta').getBoundingClientRect();"
        " return box.bottom < 0 || box.top > innerHeight; }"
    )


@pytest.mark.parametrize("width", [390, 761, 900])
def test_the_bar_follows_the_checked_plan_and_adds_it(browser, live_server, product, width):
    SHOTS.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(viewport={"width": width, "height": 844}, has_touch=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    url = live_server.url + product.get_absolute_url()
    bar = page.locator("[data-buy-bar]")
    region = page.get_by_role("region", name="خرید سریع")
    button = region.get_by_role("button", name="افزودن به سبد خرید")
    cta = page.locator(".buybox__cta")
    try:
        page.goto(url)
        expect(bar).to_be_attached()
        expect(cta).to_be_in_viewport()
        settle(page)
        expect(bar).to_be_hidden()

        leave_the_buy_box(page)
        expect(cta).not_to_be_in_viewport()
        expect(region).to_be_visible()
        expect(region.locator("bdi")).to_have_text("یک ماهه")
        expect(region.locator(".price__num")).to_have_text("۲۰۰,۰۰۰")
        box = region.bounding_box()
        assert box["x"] == 0 and box["width"] == width
        assert abs(box["y"] + box["height"] - PHONE["height"]) < 1
        assert button.bounding_box()["height"] >= 44
        assert region.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(255, 255, 255)"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.url == url
        expect(page.locator("h1")).to_have_text("Spotify Premium")
        page.screenshot(path=str(SHOTS / "bar-390.png"))

        # The page's last lines scroll clear of the bar instead of sitting under it.
        scroll_to(page, 10**6)
        expect(region).to_be_visible()
        footer = page.locator(".v-footer").bounding_box()
        assert footer["y"] + footer["height"] <= region.bounding_box()["y"] + 0.5
        page.screenshot(path=str(SHOTS / "footer-390.png"))

        scroll_to(page, 0)
        expect(bar).to_be_hidden()
        page.locator(".plan").filter(has_text="سه ماهه").tap()
        expect(page.locator("input[name=plan]").nth(1)).to_be_checked()
        leave_the_buy_box(page)
        expect(region.locator("bdi")).to_have_text("سه ماهه")
        expect(region.locator(".price__num")).to_have_text("۵۰۰,۰۰۰")
        page.screenshot(path=str(SHOTS / "other-plan-390.png"))

        # While the request is out the bar's button is as unavailable as the box's.
        held = []
        page.route("**/cart/add/", lambda route: held.append(route))
        button.tap()
        expect(button).to_have_attribute("aria-disabled", "true")
        expect(cta).to_have_attribute("aria-disabled", "true")
        held[0].continue_()
        page.unroute("**/cart/add/")
        expect(page.locator("#cart-toast")).to_be_visible()
        expect(page.locator(".v-cart__count")).to_have_text("۱")
        assert [item.plan.title for item in CartItem.objects.all()] == ["سه ماهه"]
        expect(button).to_have_attribute("aria-disabled", "false")
        expect(region).to_be_visible()
        assert not errors
    finally:
        context.close()


def test_a_filled_plan_takes_the_bar_away_and_keeps_focus(browser, live_server, product):
    page = browser.new_page(viewport=PHONE)
    try:
        page.goto(live_server.url + product.get_absolute_url())
        leave_the_buy_box(page)
        region = page.get_by_role("region", name="خرید سریع")
        region.get_by_role("button", name="افزودن به سبد خرید").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#cart-toast")).to_be_visible()
        expect(page.locator(".v-cart__count")).to_have_text("۱")
        # The monthly plan allows one, so the box offers the cart instead and the bar goes.
        expect(page.locator(".buybox__cart")).to_be_visible()
        expect(page.locator("[data-buy-bar]")).to_be_hidden()
        expect(page.locator(".buybox__cart")).to_be_focused()
        # The link it lands on is brought into view, clear of the header, so the ring is seen.
        expect(page.locator(".buybox__cart")).to_be_in_viewport()
        wait_until_on_top(page, ".buybox__cart", ".buybox__cart")
    finally:
        page.close()


@pytest.mark.parametrize("name,opened", [("phone", PHONE), ("narrowed", DESKTOP)])
def test_a_button_under_the_returned_header_is_out_of_sight(
    browser, live_server, product, name, opened
):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport=opened)
    bar = page.locator("[data-buy-bar]")
    try:
        page.goto(live_server.url + product.get_absolute_url())
        # Opened wide and then narrowed, the phone's taller header is the one to clear.
        page.set_viewport_size(PHONE)
        leave_the_buy_box(page)
        expect(bar).to_be_visible()
        expect(page.locator(".v-header")).to_have_class(re.compile("v-header--away"))
        # Scrolling back up brings the header back over the button: wholly, then all but a sliver.
        for rise in (90, 4):
            raise_cta_to_header_edge(page, rise)
            wait_until_on_top(page, ".buybox__cta", ".v-header")
            settle(page)
            assert bar.is_visible(), rise
        page.screenshot(path=str(SHOTS / f"under-header-{name}-390.png"))
        # Once most of the button is clear of the header, the box's own button is the one to use.
        raise_cta_to_header_edge(page, -30)
        wait_until_on_top(page, ".buybox__cta", ".buybox__cta")
        expect(bar).to_be_hidden()
    finally:
        page.close()


def test_the_bar_follows_the_price_when_a_discount_ends(browser, live_server, product):
    product.plans.filter(title="سه ماهه").update(
        promo_ends_at=timezone.now() + timedelta(seconds=6)
    )
    page = browser.new_page(viewport=PHONE)
    region = page.get_by_role("region", name="خرید سریع")
    row = page.locator(".plan").filter(has_text="سه ماهه")
    try:
        page.goto(live_server.url + product.get_absolute_url())
        row.click()
        leave_the_buy_box(page)
        expect(region.locator(".price__num")).to_have_text("۵۰۰,۰۰۰")
        # At zero the box's countdown swaps in the regular price, and the bar must not keep the old one.
        expect(row.locator("[data-plan-price] .price__num")).to_have_text("۶۰۰,۰۰۰", timeout=10000)
        expect(region.locator(".price__num")).to_have_text("۶۰۰,۰۰۰")
        expect(region.locator("bdi")).to_have_text("سه ماهه")
        expect(region).to_be_visible()
    finally:
        page.close()


def test_a_refused_add_brings_the_box_and_its_message_into_view(browser, live_server, product):
    page = browser.new_page(viewport=PHONE)
    try:
        page.goto(live_server.url + product.get_absolute_url())
        leave_the_buy_box(page)
        # Sold out while the page was open: the server refuses, and says so in the box.
        product.plans.update(stock_quantity=0)
        page.get_by_role("region", name="خرید سریع").get_by_role(
            "button", name="افزودن به سبد خرید"
        ).click()
        error = page.locator(".cart-add-error")
        expect(error).to_be_visible()
        expect(error).not_to_have_text("")
        expect(error).to_be_in_viewport()
        expect(page.locator(".buybox__cta")).to_be_in_viewport()
        expect(page.locator("[data-buy-bar]")).to_be_hidden()
        assert not CartItem.objects.exists()
    finally:
        page.close()


def test_the_bar_leaves_from_901_pixels(browser, live_server, product):
    page = browser.new_page(viewport=PHONE)
    bar = page.locator("[data-buy-bar]")
    try:
        page.goto(live_server.url + product.get_absolute_url())
        leave_the_buy_box(page)
        expect(bar).to_be_visible()
        page.set_viewport_size({"width": 901, "height": 844})
        leave_the_buy_box(page)
        assert cta_out_of_sight(page)
        settle(page)
        expect(bar).to_be_hidden()
        page.set_viewport_size({"width": 900, "height": 844})
        leave_the_buy_box(page)
        expect(bar).to_be_visible()
    finally:
        page.close()


def test_the_bar_never_shows_on_a_wide_screen(browser, live_server, product):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport=DESKTOP)
    bar = page.locator("[data-buy-bar]")
    try:
        page.goto(live_server.url + product.get_absolute_url())
        expect(bar).to_be_attached()
        left = False
        for top in range(0, page.evaluate("document.documentElement.scrollHeight"), 150):
            scroll_to(page, top)
            settle(page)
            assert bar.is_hidden(), top
            left = left or cta_out_of_sight(page)
        # The box did leave the screen, so it is the width that keeps the bar away.
        assert left
        page.screenshot(path=str(SHOTS / "bottom-1440.png"))
    finally:
        page.close()


def test_the_bar_waits_while_nothing_is_purchasable(browser, live_server, product):
    product.plans.update(stock_quantity=0)
    page = browser.new_page(viewport=PHONE)
    bar = page.locator("[data-buy-bar]")
    try:
        page.goto(live_server.url + product.get_absolute_url())
        expect(page.locator(".buybox__cta")).to_have_text("ناموجود")
        expect(bar).to_be_attached()
        for move in (leave_the_buy_box, lambda page: scroll_to(page, 10**6)):
            move(page)
            assert cta_out_of_sight(page)
            settle(page)
            expect(bar).to_be_hidden()
        # Restocked while the page is open: the availability poll brings both buttons back.
        product.plans.update(stock_quantity=None)
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        region = page.get_by_role("region", name="خرید سریع")
        expect(region).to_be_visible()
        expect(region.locator("bdi")).to_have_text("یک ماهه")
        expect(region.locator(".price__num")).to_have_text("۲۰۰,۰۰۰")
    finally:
        page.close()

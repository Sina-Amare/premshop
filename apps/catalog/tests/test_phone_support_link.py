"""Support one tap away on every phone page, and a footer that names the only handle.

From 1150px down the header's own support block has no room, so the same link
ends the categories row; on a phone the FAQ link gives up its place there and
becomes the last row of the opened categories list. The footer says no other
account messages for the shop, because for a buyer who came from Telegram a
fake support account asking for a password is the realistic way to lose an
account. The buy bar also covers 761 to 900px, where the buy box stops sticking.
"""

import re
from itertools import pairwise
from pathlib import Path
from unittest import mock

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.catalog.models import Category, Plan, Product
from apps.core import support

pytestmark = pytest.mark.django_db

PHONE = {"width": 390, "height": 844}
SHOTS = Path("screenshots/phone-support-link")
SHELVES = [
    ("هوش مصنوعی", "ai", "ChatGPT Plus"),
    ("ابزار توسعه", "dev", "GitHub Copilot"),
    ("سرگرمی", "media", "Spotify Premium"),
    ("گیفت کارت", "gift", "Apple Gift Card"),
]
PAGES = ["/", "/c/ai/", "/search/?q=spotify", "/cart/", "/p/spotify/"]
AWAY = re.compile(r"\bv-header--away\b")


@pytest.fixture
def shop():
    """The four shelves the shop opens with, so the categories row is as full as the real one."""
    for order, (name, slug, product) in enumerate(SHELVES):
        category = Category.objects.create(name=name, slug=slug, sort_order=order)
        made = Product.objects.create(
            category=category,
            name=product,
            slug="spotify" if slug == "media" else f"{slug}-product",
            status=Product.Status.ACTIVE,
        )
        Plan.objects.create(product=made, title="یک ماهه", cost_price=1, sale_price=200000)
    # A neighbour puts a rail under the buy box, so even a sticky box can leave the screen.
    tidal = Product.objects.create(
        category=Category.objects.get(slug="media"),
        name="Tidal HiFi",
        slug="tidal",
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(product=tidal, title="یک ماهه", cost_price=1, sale_price=300000)


def name() -> str:
    return f"پشتیبانی {support.details()['hours']} در تلگرام"


def row_support(page):
    return page.locator(".v-navline").get_by_role("link", name=name(), exact=True)


def header_height(page) -> float:
    return page.locator(".v-header").bounding_box()["height"]


def shoot(page, url: str, file: str) -> None:
    """A screenshot proves only the page it shows, so its address and header are checked first."""
    assert page.url == url
    expect(page.get_by_role("banner")).to_be_visible()
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / file))


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", PAGES)
def test_support_is_one_tap_away_on_every_phone_page(browser, live_server, shop, path):
    details = support.details()
    page = browser.new_page(viewport=PHONE)
    url = live_server.url + path
    link = row_support(page)
    try:
        page.goto(url)
        expect(link).to_be_in_viewport(ratio=1)
        assert link.get_attribute("href") == details["telegram_url"]
        expect(link.locator("small")).to_have_text(details["hours"])
        box = link.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, box
        # The 44px target fits the row, so the header keeps the height every offset reads.
        assert 170 <= header_height(page) < 171, header_height(page)
        shoot(page, url, f"{path.strip('/').split('/')[0] or 'home'}-390.png")

        # The header steps aside reading down and brings the link back on the way up.
        page.evaluate("document.body.style.minHeight = '4000px'")
        page.mouse.wheel(0, 1000)
        expect(page.locator(".v-header")).to_have_class(AWAY)
        expect(link).not_to_be_in_viewport()
        page.mouse.wheel(0, -200)
        expect(link).to_be_in_viewport(ratio=1)
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_on_a_phone_the_faq_moves_into_the_categories_list(browser, live_server, shop):
    page = browser.new_page(viewport=PHONE)
    url = live_server.url + reverse("category", args=["ai"])
    try:
        page.goto(url)
        expect(page.locator(".v-navline a[href='/faq/']:visible")).to_have_count(0)
        page.locator(".v-mobile-cats summary").click()
        menu = page.get_by_role("navigation", name="دسته‌بندی محصولات در موبایل")
        last = menu.get_by_role("link").last
        expect(last).to_have_text("سؤال‌های پرتکرار")
        expect(last).to_be_in_viewport(ratio=1)
        assert last.get_attribute("href") == reverse("faq")
        assert last.bounding_box()["height"] >= 44
        shoot(page, url, "categories-open-390.png")
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [320, 360])
def test_on_a_small_phone_nothing_in_the_row_wraps_or_overflows(browser, live_server, shop, width):
    page = browser.new_page(viewport={"width": width, "height": 740})
    try:
        page.goto(live_server.url + "/")
        expect(row_support(page)).to_be_visible()
        found = page.evaluate("""() => {
            const row = document.querySelector('.v-navline__inner'), edge = row.getBoundingClientRect();
            const wrapped = [], outside = [];
            const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
            for (let node; (node = walker.nextNode());) {
              if (!node.textContent.trim() || !node.parentElement.getClientRects().length) continue;
              const range = document.createRange();
              range.selectNodeContents(node);
              if (new Set([...range.getClientRects()].map(r => Math.round(r.top))).size > 1)
                wrapped.push(node.textContent.trim());
            }
            for (const item of row.children) {
              const box = item.getBoundingClientRect();
              if (box.width && (box.left < edge.left - 0.5 || box.right > edge.right + 0.5))
                outside.push(item.className);
            }
            return {wrapped, outside, wide: document.documentElement.scrollWidth > innerWidth};
        }""")
        assert found == {"wrapped": [], "outside": [], "wide": False}, found
        assert 170 <= header_height(page) < 171, header_height(page)
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [761, 900, 1001, 1150])
def test_from_761_to_1150_the_row_holds_categories_faq_and_support(
    browser, live_server, shop, width
):
    page = browser.new_page(viewport={"width": width, "height": 900})
    url = live_server.url + "/"
    row = page.locator(".v-navline__inner")
    items = [
        row.locator(".v-desktop-cats" if width > 1000 else ".v-mobile-cats summary"),
        row.locator(".v-help"),
        row_support(page),
    ]
    try:
        page.goto(url)
        for item in items:
            expect(item).to_be_visible()
        expect(page.locator(".v-header__main .v-support")).to_be_hidden()
        # One line: every item's middle inside the row, side by side in reading order,
        # and the header as tall as it was before the link joined the row.
        edge = row.bounding_box()
        boxes = [item.bounding_box() for item in items]
        for box in boxes:
            assert edge["y"] <= box["y"] + box["height"] / 2 <= edge["y"] + edge["height"], box
        assert all(a["x"] >= b["x"] + b["width"] for a, b in pairwise(boxes)), boxes
        assert header_height(page) == 129
        if width == 900:
            shoot(page, url, "home-900.png")
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [1151, 1440])
def test_a_wide_screen_keeps_the_header_it_had(browser, live_server, shop, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    url = live_server.url + "/"
    header = page.locator(".v-header")
    try:
        page.goto(url)
        expect(page.locator(".v-header__main").get_by_role("link", name=name())).to_be_visible()
        expect(page.locator(".v-navline .v-support")).to_be_hidden()
        if width == 1440:
            shoot(page, url, "home-1440.png")
        # What this step added is out of the picture at this width: without it, the same pixels.
        before = header.screenshot()
        page.evaluate(
            "document.querySelectorAll('.v-navline .v-support, .v-mobile-cats__faq')"
            ".forEach(added => added.remove())"
        )
        assert page.locator(".v-mobile-cats__faq").count() == 0
        assert header.screenshot() == before
    finally:
        page.close()


@pytest.mark.parametrize("path", [*PAGES, "/faq/", "/search/?q=zzzz", "/login/", "/no-such-page/"])
def test_every_footer_says_the_handle_is_the_only_one(client, shop, path):
    details = support.details()
    body = client.get(path).content.decode()

    contact = re.search(r'<div class="v-footer__contact">(.*?)</div>', body, re.S).group(1)
    sentence = re.escape(details["only_handle"])
    assert re.search(rf"{re.escape(details['telegram'])}</bdi></a><p>{sentence}</p>$", contact)


def test_the_sentence_comes_from_the_support_details(client, shop):
    planted = {**support.details(), "only_handle": "planted"}
    with mock.patch.object(support, "details", return_value=planted):
        body = client.get("/").content.decode()

    assert "<p>planted</p></div>" in body
    assert "آیدی دیگری" not in body


def test_the_sentence_is_the_one_written_for_the_footer():
    assert support.details()["only_handle"] == "جز این آیدی، آیدی دیگری از طرف ما پیام نمی‌دهد."


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("width", "bar"), [(761, True), (900, True), (901, False)])
def test_the_buy_bar_covers_the_widths_where_the_buy_box_does_not_stick(
    browser, live_server, shop, width, bar
):
    page = browser.new_page(viewport={"width": width, "height": 844})
    cta = page.locator(".buybox__cta")
    try:
        page.goto(live_server.url + "/p/spotify/")
        position = page.locator(".pdp__buy").evaluate("box => getComputedStyle(box).position")
        assert position == ("static" if bar else "sticky")
        page.evaluate("scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'})")
        expect(cta).not_to_be_in_viewport()
        # Two frames and a beat, so an observer that would show the bar has reported.
        page.evaluate(
            "() => new Promise(done => requestAnimationFrame(() =>"
            " requestAnimationFrame(() => setTimeout(done, 80))))"
        )
        assert page.locator("[data-buy-bar]").is_visible() is bar
    finally:
        page.close()

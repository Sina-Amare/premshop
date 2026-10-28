"""A service the shop does not list is a question for support, asked on Telegram.

Telegram used to appear only on an empty search. The home page's category
list and every category grid now end with a quiet row that asks, the footer's
«راهنما» list carries «درخواست محصول», and an empty search hands the query to
Telegram as a draft. Telegram documents that draft for username links,
`t.me/<username>?text=<draft_text>`: "UTF-8 text to pre-enter into the text
input bar, if the user can write in the chat" (core.telegram.org/api/links).
"""

from __future__ import annotations

import html
import re
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

from apps.catalog.models import Category, Plan, Product
from apps.catalog.tests.test_storefront_browser import widest_overflow
from apps.catalog.tests.test_storefront_copy import offences
from apps.core import support
from apps.core.tests.test_accessibility_browser import STYLED

pytestmark = pytest.mark.django_db

QUESTION = "سرویسی که می‌خواهید در فهرست نیست؟"
ASK = "در تلگرام بپرسید"
# The feature's own list, beside the house list that `offences` applies.
BANNED = ["بهترین", "سریع‌ترین", "ارزان‌تر", "ضمانت", "تضمین", "بازگشت وجه", "درگاه"]
NOT_IN_COPY = re.compile(r"[—–!\U0001F000-\U0001FAFF☀-➿]")


@pytest.fixture
def shop():
    category = Category.objects.create(name="سرگرمی", slug="fun")
    product = Product.objects.create(
        category=category,
        name="Netflix",
        slug="netflix",
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(
        product=product, title="۱ ماهه", cost_price=Decimal(1), sale_price=Decimal(100000)
    )


def request_rows(body: str) -> list[str]:
    return re.findall(r'<div class="v-request">(.*?)</div>', body, re.S)


def text_of(markup: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def recovery_href(body: str) -> str:
    block = re.search(r'<div class="v-search-recovery">(.*?)</div>', body, re.S)
    assert block, "the empty search lost its Telegram block"
    return html.unescape(re.search(r'href="([^"]+)"', block.group(1)).group(1))


def footer_guide(body: str) -> str:
    guide = body[body.index('aria-label="راهنمای فروشگاه"') :]
    return guide[: guide.index("</nav>")]


def guide_links(guide: str) -> dict[str, str]:
    found = re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', guide, re.S)
    return {text_of(name): href for href, name in found}


def draft_of(href: str) -> str:
    return parse_qs(urlsplit(href).query)["text"][0]


def assert_asks_support(row: str) -> None:
    assert QUESTION in text_of(row)
    link = re.search(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
    assert link, row
    assert link.group(1) == support.details()["telegram_url"]
    assert text_of(link.group(2)) == ASK


def test_the_home_category_list_ends_with_the_request_row(client, shop):
    body = client.get(reverse("home")).content.decode()

    (row,) = request_rows(body)
    assert_asks_support(row)
    listing = body[body.index('class="v-category-links"') :]
    listing = listing[: listing.index("</section>")]
    assert listing.rindex('href="/c/') < listing.index('class="v-request"'), "not the last row"


def test_a_category_grid_ends_with_the_request_row(client, shop):
    body = client.get(reverse("category", args=["fun"])).content.decode()

    (row,) = request_rows(body)
    assert_asks_support(row)
    page = body[body.index('<div class="grid">') : body.index("</main>")]
    assert page.rindex('class="pcard"') < page.index('class="v-request"'), "not after the grid"


def test_the_footer_guide_links_a_product_request_to_support(client, shop):
    guide = footer_guide(client.get(reverse("home")).content.decode())

    assert guide_links(guide).get("درخواست محصول") == support.details()["telegram_url"]


@pytest.mark.parametrize("query", ["chatgpt team", "Disney+", 'AT&T "x" <b>', "اسپاتیفای"])
def test_an_empty_search_asks_on_telegram_with_the_query_as_a_draft(client, shop, query):
    body = client.get(reverse("search"), {"q": query}).content.decode()
    assert "محصولی پیدا نکردیم" in body, "the search found something"

    href = recovery_href(body)
    url = urlsplit(href)
    assert href.startswith(support.details()["telegram_url"] + "?text=")
    # Percent-encoded throughout: some decoders read a bare «+» as a space.
    assert " " not in href and "+" not in url.query
    assert parse_qs(url.query) == {"text": [f"سلام، دنبال \u2068{query}\u2069 هستم."]}


def test_a_search_with_results_shows_no_draft_link(client, shop):
    body = client.get(reverse("search"), {"q": "netflix"}).content.decode()

    assert "?text=" not in body


def test_the_new_copy_is_written_persian_with_no_banned_word(client, shop):
    home = client.get(reverse("home")).content.decode()
    miss = client.get(reverse("search"), {"q": "chatgpt team"}).content.decode()
    (row,) = request_rows(home)
    copy = [
        text_of(row),
        *[name for name in guide_links(footer_guide(home)) if "درخواست" in name],
        draft_of(recovery_href(miss)),
    ]

    for text in copy:
        assert not [word for word in BANNED if word in text], text
        assert not NOT_IN_COPY.search(text), text
        assert not offences(text, written=True), text


def test_the_copy_check_would_catch_a_banned_word():
    """The control: each check fires on the kind of line it exists to stop."""
    assert [word for word in BANNED if word in "با ضمانت بازگشت وجه"]
    assert NOT_IN_COPY.search("بپرسید!") and NOT_IN_COPY.search("تلگرام — پشتیبانی")
    assert offences("سرویس رو می‌خواید؟", written=True)


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("path", ["/", "/c/fun/", "/search/?q=chatgpt"])
def test_the_ask_link_fits_and_can_be_tapped(browser, live_server, shop, width, path):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}{path}")
        assert page.evaluate(STYLED), "a stylesheet did not load"
        link = page.locator(".v-request a, .v-search-recovery a")
        assert link.count() == 1, path
        box = link.bounding_box()
        assert link.is_visible() and box, path
        assert box["x"] >= 0 and box["x"] + box["width"] <= width, box
        assert box["height"] >= 44, "smaller than a fingertip target"
        assert not widest_overflow(page), path
        assert page.evaluate("document.documentElement.scrollWidth") <= width, path
    finally:
        context.close()

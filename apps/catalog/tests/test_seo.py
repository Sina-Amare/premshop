"""What a crawler and a link preview read: addresses, titles, images, headings."""

from __future__ import annotations

import re
from decimal import Decimal
from html import unescape

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse
from PIL import Image

from apps.catalog.models import Category, Plan, Product

pytestmark = pytest.mark.django_db

SITE = "https://premshop.ir"
RLM = "\N{RIGHT-TO-LEFT MARK}"


@pytest.fixture(autouse=True)
def site(settings):
    settings.SITE_URL = SITE


@pytest.fixture
def shop():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        short_description="اشتراک Claude Pro روی اکانت خودتان.",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(product=product, title="۱ ماهه", cost_price=1, sale_price=Decimal(900000))
    return product


def head(body: str) -> str:
    return body.split("</head>", 1)[0]


def meta(body: str, prop: str) -> str | None:
    found = re.search(rf'<meta (?:property|name)="{re.escape(prop)}" content="([^"]*)"', body)
    return unescape(found[1]) if found else None


def canonical(body: str) -> str | None:
    found = re.search(r'<link rel="canonical" href="([^"]*)"', body)
    return unescape(found[1]) if found else None


def title(body: str) -> str:
    return unescape(re.search(r"<title>(.*?)</title>", body, re.S)[1])


INDEXED = {
    "home": lambda shop: reverse("home"),
    "department": lambda shop: reverse("category", args=[shop.category.slug]),
    "product": lambda shop: reverse("product", args=[shop.slug]),
    "faq": lambda shop: reverse("faq"),
}


@pytest.mark.parametrize("page", INDEXED)
def test_the_canonical_address_carries_no_query_string(client, shop, page):
    """A Telegram link with utm parameters is otherwise a second copy of the page."""
    path = INDEXED[page](shop)

    body = client.get(path + "?utm_source=telegram&utm_medium=channel").content.decode()

    assert canonical(body) == SITE + path
    assert meta(body, "og:url") == SITE + path


@pytest.mark.parametrize("page", INDEXED)
def test_absolute_addresses_come_from_site_url_whatever_host_was_asked(
    client, settings, shop, page
):
    """The Host header is whatever the client sent; a page that echoes it can
    be made to name another site as its own."""
    settings.ALLOWED_HOSTS = ["*"]

    body = head(client.get(INDEXED[page](shop), HTTP_HOST="attacker.example").content.decode())

    assert canonical(body).startswith(SITE + "/")
    assert meta(body, "og:url").startswith(SITE + "/")
    assert meta(body, "og:image").startswith(SITE + "/static/img/og-")
    assert "attacker.example" not in body


@pytest.mark.parametrize("path", ["/search/?q=claude", "/login/", "/login/code/", "/no-such-page/"])
def test_a_page_that_is_not_indexed_names_no_canonical_address(client, shop, path):
    body = client.get(path).content.decode()

    assert '<meta name="robots" content="noindex">' in body
    assert canonical(body) is None
    assert meta(body, "og:url") is None


ALL_PAGES = {
    **INDEXED,
    "search": lambda shop: reverse("search") + "?q=claude",
    "search miss": lambda shop: reverse("search") + "?q=xbox",
    "sign-in": lambda shop: reverse("login"),
    "code request": lambda shop: reverse("login-code"),
    "404": lambda shop: "/no-such-page/",
}


@pytest.mark.parametrize("page", ALL_PAGES)
def test_every_title_names_the_shop_once_after_a_bar(client, shop, page):
    body = client.get(ALL_PAGES[page](shop)).content.decode()

    assert title(body).count("PremShop") == 1
    assert title(body).endswith(" | PremShop")
    assert meta(body, "og:title") == title(body)
    assert meta(body, "og:description") == meta(body, "description")


def test_the_seo_title_is_the_part_before_the_bar(client, shop):
    shop.seo_title = "خرید اشتراک Claude Pro (کلاود پرو)"
    shop.save()
    shop.category.seo_title = "خرید اشتراک هوش مصنوعی"
    shop.category.save()

    product = client.get(reverse("product", args=[shop.slug])).content.decode()
    department = client.get(reverse("category", args=[shop.category.slug])).content.decode()

    assert title(product) == "خرید اشتراک Claude Pro (کلاود پرو) | PremShop"
    assert title(department) == "خرید اشتراک هوش مصنوعی | PremShop"


def test_without_a_seo_title_the_name_is_the_title_and_a_latin_end_is_marked(client, shop):
    body = client.get(reverse("product", args=[shop.slug])).content.decode()

    assert title(body) == f"Claude Pro{RLM} | PremShop"


def test_the_preview_card_and_the_icons_are_pngs_of_the_size_they_claim(client):
    body = client.get(reverse("home")).content.decode()
    linked = re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="/static/([^"]+)"', body)

    sizes = {}
    for name in [*linked, "img/og-default.png"]:
        path = finders.find(name)
        assert path, f"{name} is linked but not in static/"
        with Image.open(path) as image:
            sizes[name] = (image.format, image.size)

    assert sizes == {
        "img/favicon-32.png": ("PNG", (32, 32)),
        "img/apple-touch-icon.png": ("PNG", (180, 180)),
        "img/og-default.png": ("PNG", (1200, 630)),
    }
    assert meta(body, "og:image:width") == "1200" and meta(body, "og:image:height") == "630"


def test_favicon_ico_answers_at_the_root(client):
    """Browsers and crawlers ask for it by that name, whatever the page links."""
    response = client.get("/favicon.ico")

    assert response.status_code == 302
    target = response["Location"]
    assert target == "/static/img/favicon.ico"
    with Image.open(finders.find("img/favicon.ico")) as icon:
        assert icon.format == "ICO"
        assert {(16, 16), (32, 32)} <= icon.info["sizes"]


def heading_jumps(body: str) -> list[tuple[int, int]]:
    levels = [int(n) for n in re.findall(r"<h([1-6])[\s>]", body)]
    assert levels and levels[0] == 1, levels
    return [(a, b) for a, b in zip(levels, levels[1:], strict=False) if b > a + 1]


@pytest.mark.parametrize("page", ALL_PAGES)
def test_no_page_skips_a_heading_level(client, shop, page):
    """A card's name is an h3, so a grid straight under the page's h1 jumped a level."""
    Product.objects.create(
        category=shop.category,
        name="Claude Max",
        slug="claude-max",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        status=Product.Status.ACTIVE,
    )

    assert heading_jumps(client.get(ALL_PAGES[page](shop)).content.decode()) == []


def test_a_product_shares_its_own_preview_and_otherwise_the_shops(client, shop):
    """scripts/render_og.py draws og-<slug>.png for public products only."""
    own = meta(client.get(reverse("product", args=[shop.slug])).content.decode(), "og:image")
    shop.slug = "never-drawn"
    shop.save()
    fallback = meta(client.get(reverse("product", args=[shop.slug])).content.decode(), "og:image")

    assert own == SITE + "/static/img/og-claude-pro.png"
    assert fallback == SITE + "/static/img/og-default.png"

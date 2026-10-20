"""Every link on the pages a buyer sees goes somewhere that exists.

The footer used to point every entry at `/`, so «شرایط بازگشت وجه» landed on
the home page; the owner's rule since is that nothing links to a page that
has not been built. This follows every internal link on the key pages, and
checks every in-page anchor against an id on the same page.
"""

from __future__ import annotations

from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from django.urls import reverse

from apps.catalog.models import Category, Plan, Product
from apps.core import support

pytestmark = pytest.mark.django_db


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if found.get("id"):
            self.ids.add(found["id"] or "")
        if tag == "a" and found.get("href"):
            self.hrefs.append(found["href"] or "")


@pytest.fixture
def shop():
    category = Category.objects.create(name="سرگرمی", slug="fun")
    for slug in ("netflix", "spotify"):
        product = Product.objects.create(
            category=category,
            name=slug,
            slug=slug,
            delivery_type=Product.DeliveryType.READY_ACCOUNT,
            status=Product.Status.ACTIVE,
        )
        Plan.objects.create(
            product=product,
            title="۱ ماهه",
            cost_price=Decimal(1),
            sale_price=Decimal(100000),
            promo_price=Decimal(90000),
        )


PAGES = [
    "/",
    "/c/fun/",
    "/p/netflix/",
    "/faq/",
    "/search/?q=netflix",
    "/search/?q=zzzz",
    "/no-such-page/",
    "/login/",
    "/login/code/",
]


def dead_links(client, html: str) -> list[str]:
    parser = _Links()
    parser.feed(html)
    assert parser.hrefs, "no links read; the parser saw nothing"
    dead = []
    for href in parser.hrefs:
        if urlsplit(href).scheme in {"http", "https"}:
            # The one place the shop sends a buyer off-site.
            if href != support.details()["telegram_url"]:
                dead.append(f"{href}: an external link nobody reviewed")
        elif href.startswith("#"):
            if href[1:] not in parser.ids:
                dead.append(f"{href}: no element has that id")
        elif (status := client.get(href, follow=True).status_code) != 200:
            dead.append(f"{href}: {status}")
    return dead


@pytest.mark.parametrize("page", PAGES)
def test_every_link_on_the_page_resolves(client, shop, page):
    assert not dead_links(client, client.get(page).content.decode())


def test_the_checker_catches_each_kind_of_dead_link(client, shop):
    """The control: a page that does not exist, an anchor with no target and
    an unreviewed external link, beside one link that is fine."""
    planted = (
        f'<a href="{reverse("home")}">ok</a><a href="/terms/">x</a>'
        '<a href="#nowhere">x</a><a href="https://example.com/">x</a>'
    )

    assert len(dead_links(client, planted)) == 3

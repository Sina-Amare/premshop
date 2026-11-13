"""The storefront ships no inline code, so a content security policy can refuse it.

Two things are allowed. The home page's orbit script is inline on purpose: the
head holds the first paint until it has placed the logos, and the launch step
allows it by its hash. And a style attribute that only sets `--brand`, a
product's own colour, which a class cannot hold; the launch step moves those
into one nonce-carrying style block. A JSON-LD block is data, not a script.

The scan reads script elements and style attributes only. A `<style>` element,
such as the product page's `<noscript>` rule, is not read.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import timedelta
from html.parser import HTMLParser
from typing import Any
from unittest import mock

import pytest
from cryptography.fernet import Fernet
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Category, Plan, Product
from apps.cms.models import Page
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db

ORBIT = "[data-hero-scene]"
BRAND_ONLY = re.compile(r"\s*--brand\s*:\s*[^;]+;?\s*")
STATUS = {"404": 404, "503": 503}
# One mark per promotion template: the card's offer line, the product page's badge,
# the monthly figure and the deadline.
PROMOTION_MARKS = ("pcard__deal", "plan__promotion", "plan__monthly", "deadline__said")


class Inline(HTMLParser):
    """The inline scripts and style attributes of one page."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self.reading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if "style" in values:
            self.styles.append(values["style"] or "")
        if tag == "script" and "src" not in values and values.get("type") != "application/ld+json":
            self.scripts.append("")
            self.reading = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.reading = False

    def handle_data(self, data: str) -> None:
        if self.reading:
            self.scripts[-1] += data


def offences(label: str, body: str) -> list[str]:
    page = Inline()
    page.feed(body)
    found = [
        f"{label}: inline script {script[:50]!r}" for script in page.scripts if ORBIT not in script
    ]
    found += [
        f"{label}: style attribute {style!r}"
        for style in page.styles
        if not BRAND_ONLY.fullmatch(style)
    ]
    return found


def test_the_scan_finds_what_it_forbids_and_lets_the_allowed_through():
    body = (
        "<script>run()</script><script src='/a.js'></script>"
        "<script type='application/ld+json'>{}</script>"
        f"<script>{ORBIT}</script>"
        "<p style='margin:0'>x</p><div style='--brand: #10a37f'>y</div>"
        "<div style='--brand: red; margin: 0'>z</div>"
    )

    assert offences("sample", body) == [
        "sample: inline script 'run()'",
        "sample: style attribute 'margin:0'",
        "sample: style attribute '--brand: red; margin: 0'",
    ]


@pytest.fixture
def shelf(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEY_FALLBACKS = []
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    plans = []
    for slug in ("one", "two"):
        product = Product.objects.create(
            category=category,
            name=slug,
            slug=slug,
            short_description="اشتراک نمونه",
            brand_colour="#10a37f",
            delivery_type=Product.DeliveryType.READY_ACCOUNT,
            status=Product.Status.ACTIVE,
        )
        plans.append(
            Plan.objects.create(
                product=product,
                title="۱ ماهه",
                cost_price=1,
                sale_price=200000,
                duration_days=30,
                max_quantity=2,
                input_type=Plan.InputType.NONE,
                telegram_required=False,
            )
        )
    # An open promotion on a longer plan, so the offer, deadline and monthly templates render.
    Plan.objects.create(
        product=plans[0].product,
        title="۳ ماهه",
        cost_price=1,
        sale_price=600000,
        promo_price=450000,
        promo_ends_at=timezone.now() + timedelta(days=3),
        duration_days=90,
        max_quantity=2,
        input_type=Plan.InputType.NONE,
        telegram_required=False,
    )
    return category, plans


def visits(client: Any, shelf: Any) -> Iterator[tuple[str, Any]]:
    category, plans = shelf
    Page.objects.update_or_create(
        slug="terms",
        defaults={
            "title": "قوانین خرید",
            "body": "## قوانین\n\nمتن نمونه.",
            "description": "قوانین خرید.",
            "is_published": True,
        },
    )
    yield "home", client.get(reverse("home"))
    yield "category", client.get(reverse("category", args=[category.slug]))
    yield "category sorted", client.get(
        reverse("category", args=[category.slug]), {"sort": "cheap"}
    )
    yield "product", client.get(reverse("product", args=[plans[0].product.slug]))
    yield "search", client.get(reverse("search"), {"q": "one"})
    yield "search without a match", client.get(reverse("search"), {"q": "zzzz"})
    yield "faq", client.get(reverse("faq"))
    yield "legal page", client.get(reverse("cms:terms"))
    yield "404", client.get("/no-such-page/")
    yield "cart, empty", client.get(reverse("cart"))
    buyer = User.objects.create_user("buyer@example.test")
    cart = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=cart, plan=plans[0], quantity=1)
    client.force_login(buyer)
    yield "cart", client.get(reverse("cart"))
    yield "checkout", client.get(reverse("checkout"))
    yield "account", client.get(reverse("account"))
    orderer = User.objects.create_user("orderer@example.test")
    order = place_order(user=orderer, lines=[OrderLine(plans[1], 1)])
    client.force_login(orderer)
    yield "orders", client.get(reverse("order-list"))
    yield "order", client.get(reverse("order-detail", args=[order.order_number]))
    client.logout()
    yield "tracking", client.get(reverse("order-tracking", args=[order.tracking_token]))
    with mock.patch("apps.core.ratelimit.cache.add", side_effect=RedisConnectionError("refused")):
        yield "503", client.post(reverse("login-code"), {"phone": "09125550012"})


def test_no_storefront_page_carries_inline_code(client, shelf):
    found = []
    for label, response in visits(client, shelf):
        assert response.status_code == STATUS.get(label, 200), (label, response.status_code)
        found += offences(label, response.content.decode())

    assert not found, found


def test_the_pages_scanned_render_every_promotion_template(client, shelf):
    pages = dict(visits(client, shelf))
    shown = pages["category"].content.decode() + pages["product"].content.decode()

    assert [mark for mark in PROMOTION_MARKS if mark not in shown] == []


def test_the_orbit_is_the_only_inline_script_and_it_is_on_the_home_page(client, shelf):
    pages = dict(visits(client, shelf))
    home = Inline()
    home.feed(pages["home"].content.decode())

    assert len(home.scripts) == 1 and ORBIT in home.scripts[0]


def test_the_empty_and_limited_states_carry_no_inline_code(client):
    Category.objects.create(name="خالی", slug="empty")
    found = offences(
        "empty category", client.get(reverse("category", args=["empty"])).content.decode()
    )
    found += offences(
        "search, nothing to sell", client.get(reverse("search"), {"q": "x"}).content.decode()
    )
    with mock.patch("apps.core.ratelimit.hit", return_value=False):
        limited = client.get(reverse("search"), {"q": "x"})
    assert limited.status_code == 429
    found += offences("search, limited", limited.content.decode())

    assert not found, found

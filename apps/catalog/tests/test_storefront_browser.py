"""The storefront in a real Chromium: the countdown, and the phone's width.

Two things the test client cannot see. The discount clock is JavaScript, so
nothing below the template is proof that it runs. And horizontal overflow is
a layout result, not markup: the deadline badge shipped as one nowrap element
too many in the buy box, which pushed the whole product page sideways at
390px, and no assertion over HTML would have noticed.

Skipped when Playwright's Chromium is not installed, so CI without a browser
stays green and honest rather than red and ignored.
"""

from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

PHONE = {"width": 390, "height": 844}


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    previous = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    with playwright.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 — a missing browser is a skip, not a failure
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        yield chromium
        chromium.close()
    if previous is None:
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
    else:
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = previous


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
        assert "مونده" in first
        assert "۰۱:5" in first.replace("۵", "5"), first

        page.wait_for_timeout(2100)
        assert badge.text_content() != first, "the clock is painted once and never moves"
    finally:
        context.close()

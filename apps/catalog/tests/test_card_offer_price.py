"""A card's offer line prices the discounted plan, not the cheapest one.

The line used to read «۳ ماهه با ۱۴٪ تخفیف» over «از ۱۹۰,۰۰۰», which was the
cheapest plan's undiscounted price, and buyers took 190,000 for the offer.
"""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product
from apps.catalog.templatetags.prices import card_offer
from apps.core.tests.test_accessibility_browser import STYLED

CARD = re.compile(r'<article class="pcard".*?</article>', re.S)
PROMOTION = re.compile(r'<div class="pcard__promotion".*?</div>', re.S)
STARTING = re.compile(r'<div class="pcard__price-state">.*?</div>', re.S)
STRUCK = re.compile(r"<del\b[^>]*>(.*?)</del>", re.S)
# Home, the category and a search: every grid a card is shown in.
PAGES = ["/", "/c/ai/", "/search/?q=Claude"]
PHONE = {"width": 390, "height": 844}


def make_product(month: dict, quarter: dict) -> Product:
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        status=Product.Status.ACTIVE,
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
    )
    Plan.objects.create(product=product, title="۱ ماهه", cost_price=1, **month)
    Plan.objects.create(product=product, title="۳ ماهه", cost_price=1, sort_order=1, **quarter)
    return product


def in_two_days() -> dict:
    return {"promo_ends_at": timezone.now() + timedelta(days=2)}


@pytest.fixture
def quarter_on_offer(db):
    return make_product(
        {"sale_price": 190000}, {"sale_price": 490000, "promo_price": 420000, **in_two_days()}
    )


@pytest.mark.parametrize("path", PAGES)
def test_the_offer_line_prices_the_discounted_plan_beside_its_struck_original(
    client, quarter_on_offer, path
):
    cards = CARD.findall(client.get(path).content.decode())
    assert cards, "positive control: the page shows the product"
    for card in cards:
        promotion = PROMOTION.search(card).group()
        assert "۳ ماهه" in promotion
        assert ["۴۹۰,۰۰۰" in struck for struck in STRUCK.findall(promotion)] == [True]
        assert "۴۲۰,۰۰۰" in STRUCK.sub("", promotion)
        starting = STARTING.search(card).group()
        assert "۱ ماهه" in starting and "از" in starting and "۱۹۰,۰۰۰" in starting
        assert "<del" not in starting
        assert "۱۹۰,۰۰۰" not in "".join(STRUCK.findall(card))


def test_a_discounted_cheapest_plan_prints_its_price_once(client, db):
    make_product(
        {"sale_price": 190000, "promo_price": 150000, **in_two_days()}, {"sale_price": 490000}
    )
    cards = CARD.findall(client.get(reverse("category", args=["ai"])).content.decode())
    assert cards
    for card in cards:
        # A <template> holds the line that follows the expiry, not what is shown.
        shown = re.sub(r"<template\b.*?</template>", "", card, flags=re.S)
        assert shown.count("۱۵۰,۰۰۰") == 1
        assert "<del" not in shown and "۱۹۰,۰۰۰" not in shown
        assert "با ۲۱٪ تخفیف" in PROMOTION.search(shown).group()


def test_an_offer_knows_when_an_expiry_hands_it_the_starting_line(db):
    """The cheaper monthly offer ends first; from then on the «از» line names
    the quarter, so the quarter's own line must stop repeating that price."""
    now = timezone.now()
    product = make_product(
        {"sale_price": 390000, "promo_price": 312000, "promo_ends_at": now + timedelta(hours=1)},
        {"sale_price": 450000, "promo_price": 330000, "promo_ends_at": now + timedelta(hours=4)},
    )
    month, quarter = product.plans.order_by("sort_order")
    assert not card_offer(month, product)["priced"]
    line = card_offer(quarter, product)
    assert line["priced"] and 3590 <= line["until"] <= 3600


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", PAGES)
def test_the_offer_line_stays_inside_its_card_on_a_phone(browser, live_server, path):
    make_product(
        {"sale_price": 1900000},
        {"sale_price": 12500000, "promo_price": 9900000, **in_two_days()},
    )
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}{path}")
        assert page.evaluate(STYLED)
        deals = page.locator(".pcard__promotion:visible .pcard__deal")
        assert deals.count(), "positive control: a priced offer line is on the page"
        for index in range(deals.count()):
            deal = deals.nth(index)
            card = deal.locator("xpath=ancestor::article[1]").bounding_box()
            for part in (
                deal,
                *(
                    deal.locator(name)
                    for name in (".price__was", ".pcard__deal-now", ".price__cur")
                ),
            ):
                box = part.bounding_box()
                assert box["x"] >= card["x"] + 8, (path, box, card)
                assert box["x"] + box["width"] <= card["x"] + card["width"] - 8, (path, box, card)
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_the_offer_line_drops_its_figures_once_the_starting_line_names_its_plan(
    browser, live_server
):
    now = timezone.now()
    make_product(
        {"sale_price": 390000, "promo_price": 312000, "promo_ends_at": now + timedelta(hours=1)},
        {"sale_price": 450000, "promo_price": 330000, "promo_ends_at": now + timedelta(hours=4)},
    )
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(f"{live_server.url}/c/ai/")
        card = page.locator(".pcard").first
        deal = card.locator(".pcard__promotion:visible .pcard__deal")
        assert "۳۳۰,۰۰۰" in deal.text_content(), "positive control"
        assert card.locator(".pcard__from bdi").text_content() == "۱ ماهه"

        page.clock.fast_forward("01:30:00")

        assert card.locator(".pcard__from bdi").text_content() == "۳ ماهه"
        assert card.locator(".pcard__price .price__num").text_content() == "۳۳۰,۰۰۰"
        assert deal.text_content().strip() == "۳ ماهه با ۲۶٪ تخفیف"
        assert card.locator(".price__was").count() == 0
    finally:
        context.close()

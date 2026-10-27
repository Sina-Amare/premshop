import re

import pytest
from playwright.sync_api import expect

from apps.catalog.models import Category, Plan, Product
from apps.core.tests.test_accessibility_browser import STYLED

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.fixture
def products():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    for index in range(8):
        product = Product.objects.create(
            category=category,
            name="ChatGPT Plus چت جی‌پی‌تی پلاس" if index == 0 else f"Service {index}",
            slug=f"service-{index}",
            status="active",
        )
        Plan.objects.create(product=product, title="یک‌ماهه", cost_price=1, sale_price=1234567)


@pytest.mark.parametrize("width", [320, 390, 760])
def test_phone_category_has_four_cards_in_two_columns_and_a_category_link(
    browser, live_server, products, width
):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        page.evaluate("() => document.fonts.ready")
        section = page.locator(".v-category-section").first
        cards = section.locator(".pcard:visible")
        expect(cards).to_have_count(4)
        boxes = [card.bounding_box() for card in cards.all()]
        assert abs(boxes[0]["y"] - boxes[1]["y"]) < 1
        assert boxes[2]["y"] > boxes[0]["y"]
        expect(section.locator(".v-view-all")).to_have_attribute("href", "/c/ai/")
        assert section.locator("[data-rail-track]").get_attribute("tabindex") == "-1"
        for card in cards.all():
            art = card.locator(".pcard__art").bounding_box()
            assert abs(art["width"] / art["height"] - 1.6) < 0.03
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    finally:
        page.close()


def test_orbit_pause_survives_paging_and_reload_and_resume_works_with_focus(
    browser, live_server, products
):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(live_server.url + "/")
        scene = page.locator("[data-hero-scene]")
        expect(scene).to_have_attribute("data-orbit-mode", "orbit")
        pause = page.get_by_role("button", name="توقف حرکت", exact=True)
        pause.click()
        item = page.locator(".nebula__item:visible").first
        stopped = item.get_attribute("style")
        page.mouse.move(0, 0)
        page.wait_for_timeout(350)
        assert item.get_attribute("style") == stopped
        if page.locator("[data-orbit-next]").is_visible():
            page.locator("[data-orbit-next]").click()
            item = page.locator(".nebula__item:visible").first
            stopped = item.get_attribute("style")
            page.wait_for_timeout(350)
            assert item.get_attribute("style") == stopped
        page.reload()
        expect(scene).to_have_class(re.compile(r"\bis-paused\b"))
        resume = page.locator("[data-orbit-toggle]")
        expect(resume).to_have_text("ادامه‌ی حرکت")
        resume.focus()
        page.keyboard.press("Enter")
        expect(resume).to_be_focused()
        item = page.locator(".nebula__item:visible").first
        stopped = item.get_attribute("style")
        page.wait_for_timeout(350)
        assert item.get_attribute("style") != stopped
        page.emulate_media(reduced_motion="reduce")
        expect(page.locator("[data-orbit-toggle]")).to_be_hidden()
    finally:
        page.close()

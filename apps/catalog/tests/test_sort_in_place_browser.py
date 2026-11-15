"""Sorting a department reorders its cards in place: the page neither reloads nor jumps."""

import pytest
from playwright.sync_api import expect

from apps.catalog.models import Category
from apps.catalog.tests.test_category_sort import make

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

NAMES = ".grid > .pcard .pcard__name"


@pytest.mark.parametrize("width", [390, 1440])
def test_sorting_reorders_the_same_cards_without_a_reload_or_a_jump(browser, live_server, width):
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    make(category, "dear", 900000, days_old=3)
    make(category, "cheap", 100000, days_old=2)
    make(category, "middle", 500000, days_old=1)
    page = browser.new_page(viewport={"width": width, "height": 700})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"{live_server.url}/c/ai/")
        expect(page.locator(NAMES)).to_have_text(["middle", "cheap", "dear"])
        page.locator(".sort").scroll_into_view_if_needed()
        # A value set on this document survives only if no new page replaced it.
        page.evaluate("window.stayed = true")
        before = page.evaluate("scrollY")
        card = page.locator(".grid > .pcard").filter(has_text="cheap")
        card.evaluate("node => { node.dataset.same = 'yes'; }")

        page.get_by_role("link", name="ارزان‌ترین", exact=True).click()

        expect(page).to_have_url(f"{live_server.url}/c/ai/?sort=cheap")
        expect(page.locator(NAMES)).to_have_text(["cheap", "middle", "dear"])
        expect(page.locator('.sort__link[aria-current="true"]')).to_have_text("ارزان‌ترین")
        assert page.evaluate("window.stayed === true"), "the page reloaded"
        assert abs(page.evaluate("scrollY") - before) <= 1, "the page jumped"
        # The very card moved, not a copy: its clock and options button keep working.
        expect(
            page.locator('.grid > .pcard[data-same="yes"]').locator(".pcard__name")
        ).to_have_text("cheap")
        expect(page.get_by_role("link", name="ارزان‌ترین", exact=True)).to_be_focused()

        page.go_back()
        expect(page).to_have_url(f"{live_server.url}/c/ai/")
        expect(page.locator(NAMES)).to_have_text(["middle", "cheap", "dear"])
        assert page.evaluate("window.stayed === true"), "going back reloaded the page"
        assert not errors, errors
    finally:
        page.close()


def test_sorting_still_works_as_a_link_without_a_script(browser, live_server):
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    make(category, "dear", 900000, days_old=2)
    make(category, "cheap", 100000, days_old=1)
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/c/ai/")
        page.get_by_role("link", name="ارزان‌ترین", exact=True).click()
        expect(page).to_have_url(f"{live_server.url}/c/ai/?sort=cheap")
        expect(page.locator(NAMES)).to_have_text(["cheap", "dear"])
    finally:
        context.close()

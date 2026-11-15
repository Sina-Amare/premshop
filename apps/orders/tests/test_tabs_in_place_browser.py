"""Switching the order list's tabs changes the list in place: no reload, no jump."""

import pytest
from playwright.sync_api import expect

from apps.orders.services import OrderLine, place_order
from apps.orders.tests.test_customer_browser import context_for

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_a_tab_swaps_the_list_without_a_reload_or_a_jump(browser, live_server, buyer, plan, width):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    context = context_for(browser, live_server, buyer, width)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"{live_server.url}/account/orders/")
        cards = page.locator(".order-card--summary")
        expect(cards).to_have_count(1)
        page.locator(".order-tabs").scroll_into_view_if_needed()
        page.evaluate("window.stayed = true")
        before = page.evaluate("scrollY")

        page.locator('.order-tabs a[href="?tab=cancelled"]').click()

        expect(page).to_have_url(f"{live_server.url}/account/orders/?tab=cancelled")
        expect(cards).to_have_count(0)
        expect(page.locator('.order-tabs a[aria-current="page"]')).to_have_attribute(
            "href", "?tab=cancelled"
        )
        assert page.evaluate("window.stayed === true"), "the page reloaded"
        assert abs(page.evaluate("scrollY") - before) <= 1, "the page jumped"
        expect(page.locator('.order-tabs a[href="?tab=cancelled"]')).to_be_focused()

        page.go_back()
        expect(cards).to_have_count(1)
        expect(cards.first).to_contain_text(
            str(order.order_number).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        )
        assert page.evaluate("window.stayed === true"), "going back reloaded the page"
        assert not errors, errors
    finally:
        context.close()

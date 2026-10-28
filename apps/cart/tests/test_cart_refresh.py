import pytest
from playwright.sync_api import expect

from apps.cart.models import CartItem
from apps.catalog.models import Plan

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("quantity", ["1", "2"])
def test_typed_and_restored_quantities_control_both_stepper_limits(
    browser, live_server, plan, quantity
):
    stored = 3 if quantity == "1" else 1
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator("#qty").fill(str(stored))
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        page.goto(live_server.url + "/cart/")
        value = page.locator("input[name=quantity]")
        value.fill(quantity)
        minus = page.locator('[data-cart-step="-1"]')
        if quantity == "1":
            expect(minus).to_be_disabled()
        else:
            expect(minus).to_be_enabled()
        Plan.objects.filter(pk=plan.pk).update(max_quantity=2)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(value).to_have_value(quantity)
        expect(value).to_have_attribute("max", "2")
        if quantity == "1":
            expect(minus).to_be_disabled()
        else:
            expect(page.locator('.cart-stepper button[value="1"]')).to_be_disabled()
        assert CartItem.objects.get().quantity == stored
    finally:
        page.close()

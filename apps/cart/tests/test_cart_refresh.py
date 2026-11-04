import pytest
from playwright.sync_api import expect

from apps.cart.models import CartItem
from apps.catalog.models import Plan

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("surface", ["product", "cart"])
def test_an_old_poll_body_cannot_undo_a_successful_cart_change(
    browser, live_server, plan, width, surface
):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        if surface == "cart":
            page.locator(".buybox__cta").click()
            expect(page.locator(".v-cart__count")).to_have_text("۱")
            page.goto(live_server.url + "/cart/")

        # Headers can arrive before a slow body. Keep a real old answer in flight.
        page.evaluate("""() => {
            const original = window.fetch;
            window.fetch = async (...args) => {
                const response = await original(...args);
                if (String(args[0]).includes('/cart/availability/')) {
                    window.fetch = original;
                    const json = response.json.bind(response);
                    response.json = async () => {
                        const data = await json();
                        await new Promise(resolve => { window.releasePoll = resolve; });
                        return data;
                    };
                }
                return response;
            };
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        page.wait_for_function("typeof window.releasePoll === 'function'")
        if surface == "product":
            page.locator(".buybox__cta").click()
            expected = "۱"
        else:
            page.locator('.cart-stepper button[value="1"]').click()
            expected = "۲"
        expect(page.locator(".v-cart__count")).to_have_text(expected)
        expect(page.locator('[aria-busy="true"]')).to_have_count(0)

        page.evaluate("window.releasePoll()")
        expect(page.locator(".v-cart__count")).to_have_text(expected)
        assert CartItem.objects.get().quantity == (1 if surface == "product" else 2)
        if surface == "product":
            expect(page.locator("#qty")).to_have_attribute("max", "2")
        else:
            expect(page.locator('input[name="quantity"]')).to_have_value("2")
    finally:
        page.close()


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

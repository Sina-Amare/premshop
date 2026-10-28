import pytest
from playwright.sync_api import expect

from apps.catalog.models import Plan

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def test_same_count_changes_in_another_tab_refresh_line_quantities_and_totals(
    browser, live_server, plan
):
    other = Plan.objects.create(
        product=plan.product,
        title="سه‌ماهه",
        duration_days=90,
        cost_price=100000,
        sale_price=300000,
        max_quantity=3,
    )
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        token = page.locator("[name=csrfmiddlewaretoken]").input_value()

        def post(route, selected, quantity):
            response = page.request.post(
                live_server.url + "/cart/" + route + "/",
                form={
                    "plan": str(selected.pk),
                    "quantity": str(quantity),
                    "csrfmiddlewaretoken": token,
                },
                headers={"Accept": "application/json"},
            )
            assert response.ok

        post("add", other, 2)
        page.goto(live_server.url + "/cart/")
        expect(page.locator(f"#quantity-{plan.pk}")).to_have_value("1")
        expect(page.locator(f"#quantity-{other.pk}")).to_have_value("2")
        post("quantity", plan, 2)
        post("quantity", other, 1)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator(f"#quantity-{plan.pk}")).to_have_value("2")
        expect(page.locator(f"#quantity-{other.pk}")).to_have_value("1")
        expect(page.locator(".cart-summary__total")).to_contain_text("۷۰۰,۰۰۰")
        expect(page.locator(".v-cart__count")).to_have_text("۳")
    finally:
        page.close()


def test_focused_cart_navigation_moves_to_the_surviving_product_link_when_emptied(
    browser, live_server, plan
):
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        page.goto(live_server.url + "/cart/")
        page.locator(".cart-back").focus()
        response = page.request.post(
            live_server.url + "/cart/remove/",
            form={
                "plan": str(plan.pk),
                "csrfmiddlewaretoken": page.locator(
                    "[name=csrfmiddlewaretoken]"
                ).first.input_value(),
            },
        )
        assert response.ok
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator(".cart-empty")).to_be_visible()
        expect(page.locator(".cart-empty .btn")).to_be_focused()
    finally:
        page.close()

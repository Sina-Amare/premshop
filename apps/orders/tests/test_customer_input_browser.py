from pathlib import Path

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.orders.tests.test_customer_browser import capture, context_for
from apps.orders.tests.test_customer_input import NEW, OLD, waiting
from apps.orders.tests.test_input_clock import event_id
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("state", ["empty", "invalid", "stale", "long", "telegram", "mixed"])
def test_customer_input_states_keep_keyboard_access_and_private_responses(
    browser, live_server, buyer, plan, operator, width, state, tmp_path
):
    from apps.orders import services
    from apps.orders.models import OrderItem
    from apps.orders.tests.test_input_clock import act, purchase

    if state == "long":
        plan.product.name = "محصول نمونه با نام بلند برای نمایش اطلاعات فعال‌سازی در سفارش"
        plan.product.save()
    if state == "mixed":
        from apps.orders.tests.test_input_clock import pay
        from apps.orders.tests.test_order_detail import second_plan

        plan.input_type, plan.max_quantity = "email", 1
        plan.save()
        order = services.place_order(
            user=buyer,
            lines=[services.OrderLine(plan, 1, OLD), services.OrderLine(second_plan(), 1)],
        )
        pay(order, operator)
        item = order.items.get(plan=plan)
        act(services.request_input, item, operator)
    else:
        order, item = (
            purchase(buyer, plan, operator)
            if state == "telegram"
            else waiting(buyer, plan, operator)
        )
    context = context_for(browser, live_server, buyer, width)
    page = context.new_page()
    url = live_server.url + reverse("order-detail", args=[order.order_number])
    try:
        page.goto(url)
        expect(page).to_have_url(url)
        expect(page.locator("html")).to_have_attribute("dir", "rtl")
        assert page.locator(f'[data-input-item="{item.pk}"]').count() == 1
        expect(page.locator(f'[data-input-item="{item.pk}"]')).to_be_visible()
        assert OLD not in page.content()
        if state == "telegram":
            expect(page.locator('[name="customer_input"]')).to_have_count(0)
            expect(page.locator("[data-input-deadline]")).to_be_visible()
        else:
            field = page.locator('[name="customer_input"]')
            expect(field).to_have_value("")
            expect(page.locator('[name="expected_event_id"]')).to_have_value(str(event_id(item)))
            if state == "stale":
                act(services.resume_input, item, operator)
                act(services.request_input, item, operator)
            if state in ("invalid", "stale"):
                field.fill("invalid" if state == "invalid" else NEW)
                if state == "invalid":
                    field.evaluate("el => { el.form.noValidate = true; }")
                page.locator("[data-input-item] button").click()
                expect(page.get_by_role("alert")).to_be_visible()
            else:
                field.focus()
                page.keyboard.press("Tab")
                expect(page.locator("[data-input-item] button")).to_be_focused()
        capture(page, f"input-{state}", width, Path(tmp_path))
        if state == "empty":
            field.fill(NEW)
            page.keyboard.press("Tab")
            with page.expect_navigation() as navigation:
                page.keyboard.press("Enter")
            response = navigation.value
            assert response and "no-store" in response.headers["cache-control"]
            expect(page).to_have_url(url)
            expect(page.locator('[name="customer_input"]')).to_have_count(0)
            item = OrderItem.objects.get(pk=item.pk)
            assert item.status == "QUEUED" and item.customer_input == NEW
    finally:
        context.close()

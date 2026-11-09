import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.orders.tests.test_input_clock import event_id, purchase
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("language", ["fa", "en"])
def test_operator_input_cycle_and_customer_handoff(
    browser, live_server, buyer, plan, operator, width, language, tmp_path
):
    order, item = purchase(buyer, plan, operator, at=timezone.now())
    client = Client()
    client.force_login(operator)
    context = browser.new_context(
        viewport={"width": width, "height": 1000}, java_script_enabled=False
    )
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": settings.LANGUAGE_COOKIE_NAME, "value": language, "url": live_server.url},
        ]
    )
    page = context.new_page()
    detail = reverse("admin:orders_order_change", args=[order.pk])
    receipt = "اطلاعات رسید؛ ادامه‌ی انجام" if language == "fa" else "Details received; continue"
    request = "ثبت درخواست اطلاعات" if language == "fa" else "Record a request for details"
    heading = "اطلاعات فعال‌سازی" if language == "fa" else "Activation details"

    def shot(name, path, landmark):
        assert page.url.endswith(path)
        expect(page.get_by_text(landmark, exact=True).first).to_be_visible()
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"{name}-{language}-{width}.png"), full_page=True)

    try:
        page.goto(live_server.url + detail)
        shot("first-wait", detail, heading)
        episode = event_id(item)
        page.get_by_role("button", name=receipt, exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("button", name=request, exact=True)).to_be_visible()
        shot("received", detail, heading)
        page.get_by_role("button", name=request, exact=True).click()
        expect(page.get_by_role("button", name=receipt, exact=True)).to_be_visible()
        shot("paused", detail, heading)
        stale = reverse("admin:orders_order_input_action", args=[order.pk, item.pk, "receive"])
        response = page.request.post(
            live_server.url + stale,
            form={
                "expected_event_id": str(episode),
                "csrfmiddlewaretoken": next(
                    c["value"] for c in context.cookies() if c["name"] == "csrftoken"
                ),
            },
        )
        assert response.status == 400
        # Render the actual error response at its action URL.
        page.goto(live_server.url + detail)
        page.locator('button[name="expected_event_id"]').evaluate(
            "(el, data) => { el.value = data.id; el.setAttribute('formaction', data.url); }",
            {"id": str(episode), "url": stale},
        )
        page.get_by_role("button", name=receipt, exact=True).click()
        error = (
            "وضعیت این محصول تغییر کرده است؛ صفحه را دوباره باز کنید."
            if language == "fa"
            else "This item has changed; reopen the page."
        )
        shot("stale", stale, error)
        page.goto(live_server.url + detail)
        page.get_by_role("button", name=receipt, exact=True).click()
        expect(page.get_by_role("button", name=request, exact=True)).to_be_visible()
        item.refresh_from_db()
        assert item.status == "QUEUED" and item.sla_paused_at is None

        # A new waiting episode exercises the customer's handoff prompt.
        page.get_by_role("button", name=request, exact=True).click()
        client.force_login(buyer)
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                    "url": live_server.url,
                }
            ]
        )
        customer = reverse("order-detail", args=[order.order_number])
        page.goto(live_server.url + customer)
        prompt = "اطلاعات فعال‌سازی را در تلگرام برای پشتیبانی بفرستید."
        shot("customer", customer, prompt)
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert "expected_event_id" not in page.content()
        assert "Details received" not in page.content()
        expect(
            page.locator("#order-next").get_by_role("link", name="ارتباط با پشتیبانی", exact=True)
        ).to_have_attribute("href", "#order-help")
        page.locator("#order-next").get_by_role(
            "link", name="ارتباط با پشتیبانی", exact=True
        ).click()
        expect(page.locator("#support-identifier")).to_be_in_viewport()
        expect(page.locator("#order-help a").first).to_have_attribute(
            "href", "https://t.me/premshop_support"
        )
    finally:
        context.close()

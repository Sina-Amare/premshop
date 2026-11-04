import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.accounts.models import User
from apps.orders.services import OrderLine, place_order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_operator_records_payment_and_customer_sees_queue(
    browser, live_server, buyer, plan, width, tmp_path
):
    operator = User.objects.create_superuser("operator@example.test")
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    client = Client()
    client.force_login(operator)
    context = browser.new_context(viewport={"width": width, "height": 1000})
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def shot(name, url, landmark):
        assert page.url.endswith(url) and page.get_by_text(landmark, exact=True).first.is_visible()
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"{name}-{width}.png"), full_page=True)

    try:
        detail = reverse("admin:orders_order_change", args=[order.pk])
        page.goto(live_server.url + detail)
        shot("unpaid", detail, "پرداخت‌های سفارش")
        page.get_by_role("link", name="ثبت پرداخت خارج از درگاه", exact=True).click()
        form_url = reverse("admin:orders_order_record_manual_payment", args=[order.pk])
        expect(page.locator("#id_amount")).to_be_visible()
        shot("empty", form_url, "مبلغ دریافتی (تومان)")
        page.get_by_label("مبلغ دریافتی (تومان)", exact=True).fill("199999")
        reference = "1234567890" * 6 + "1234"
        page.get_by_label("شماره‌ی پیگیری بانک", exact=True).fill(reference)
        page.get_by_role("button", name="ثبت پرداخت خارج از درگاه", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("مبلغ دریافتی باید دقیقاً برابر")
        expect(page.locator("#id_bank_ref")).to_have_value(reference)
        shot("mismatch", form_url, "مبلغ دریافتی (تومان)")
        page.locator("#id_amount").fill("200000")
        page.get_by_role("button", name="ثبت پرداخت خارج از درگاه", exact=True).click()
        page.wait_for_url(live_server.url + detail)
        expect(page.locator(".order-bank-reference")).to_have_text(reference)
        shot("confirmed", detail, "پرداخت‌های سفارش")
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
        expect(page.locator(".order-status").first).to_have_text("در صف انجام")
        assert reference not in page.content()
        shot("customer", customer, "در صف انجام")
        assert not errors
    finally:
        context.close()

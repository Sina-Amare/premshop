from datetime import timedelta

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.services import OrderLine, place_order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("language", ["fa", "en"])
def test_operator_records_payment_and_customer_sees_queue(
    browser, live_server, buyer, plan, width, language, tmp_path
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
    context.add_cookies(
        [{"name": settings.LANGUAGE_COOKIE_NAME, "value": language, "url": live_server.url}]
    )
    action = (
        "ثبت پرداخت خارج از درگاه" if language == "fa" else "Record payment outside the gateway"
    )
    amount_label = "مبلغ دریافتی (تومان)" if language == "fa" else "Amount received (toman)"
    reference_label = "شماره‌ی پیگیری بانک" if language == "fa" else "Bank reference"
    history_label = "پرداخت‌های سفارش" if language == "fa" else "Order payments"
    mismatch = (
        "مبلغ دریافتی باید دقیقاً برابر"
        if language == "fa"
        else "The received amount must exactly match"
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
        shot("unpaid", detail, history_label)
        page.get_by_role("link", name=action, exact=True).click()
        form_url = reverse("admin:orders_order_record_manual_payment", args=[order.pk])
        expect(page.locator("#id_amount")).to_be_visible()
        shot("empty", form_url, amount_label)
        page.get_by_label(amount_label, exact=True).fill("199999")
        reference = "1234567890" * 6 + "1234"
        page.get_by_label(reference_label, exact=True).fill(reference)
        page.get_by_role("button", name=action, exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text(mismatch)
        expect(page.locator("#id_bank_ref")).to_have_value(reference)
        shot("mismatch", form_url, amount_label)
        page.locator("#id_amount").fill("200000")
        page.get_by_role("button", name=action, exact=True).click()
        page.wait_for_url(live_server.url + detail)
        expect(page.locator(".order-bank-reference")).to_have_text(reference)
        expect(page.get_by_role("link", name=action, exact=True)).to_have_count(0)
        shot("confirmed", detail, history_label)
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


@pytest.mark.parametrize("width", [390, 1440])
def test_ineligible_payment_actions_on_long_and_empty_orders(
    browser, live_server, buyer, plan, width, tmp_path
):
    plan.title = "پلن یک ماهه برای فعال‌سازی حساب شخصی با نام طولانی در سرویس Example Pro"
    plan.save()
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    operator = User.objects.create_superuser("operator@example.test")
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
    original = order.items.get().product_snapshot
    try:
        for state in ("expired", "telegram", "missing_input", "empty"):
            Order.objects.filter(pk=order.pk).update(
                unpaid_expires_at=timezone.now() + timedelta(hours=-1 if state == "expired" else 1)
            )
            facts = {**original}
            if state == "telegram":
                facts["telegram_required"] = True
            elif state == "missing_input":
                facts.update(input_type="email", requires_customer_input=True)
            order.items.update(product_snapshot=facts)
            current = order
            if state == "empty":
                current = Order.objects.create(user=buyer, subtotal=0, total_amount=0)
            detail = reverse("admin:orders_order_change", args=[current.pk])
            response = page.goto(live_server.url + detail)
            assert response.status == 200 and "no-store" in response.headers["cache-control"]
            assert page.url.endswith(detail)
            expect(page.get_by_text("پرداخت‌های سفارش", exact=True)).to_be_visible()
            expect(
                page.get_by_role("link", name="ثبت پرداخت خارج از درگاه", exact=True)
            ).to_have_count(0)
            page.evaluate("document.fonts.ready")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(tmp_path / f"{state}-{width}.png"), full_page=True)
    finally:
        context.close()

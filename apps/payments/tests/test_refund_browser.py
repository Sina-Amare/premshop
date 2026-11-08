import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.orders.services import OrderLine
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import paid_order
from apps.orders.tests.test_replacement_browser import keyboard_page
from apps.payments.models import Payment
from apps.payments.tests.test_refund_execution import execute
from apps.payments.tests.test_refund_execution import owed as owed

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_operator_records_a_grouped_refund_and_reconciles_it_with_the_keyboard(
    browser, live_server, owed, buyer, plan, operator, width, tmp_path
):
    order, refunds = owed
    reference = "1234567890" * 6 + "1234"
    other, _ = paid_order(buyer, operator, [OrderLine(plan, 1)])
    Payment.objects.filter(order=other).update(bank_ref=reference)
    context, page = keyboard_page(browser, live_server, operator, width)
    form_url = reverse("admin:orders_order_execute_refunds", args=[order.pk])
    detail = reverse("admin:orders_order_change", args=[order.pk])
    report = reverse("admin:orders_order_reconciliation")

    def shot(name, path, landmark):
        assert page.url.split("?")[0] == live_server.url + path
        expect(page.locator(landmark)).to_be_visible()
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"{name}-{width}.png"), full_page=True)

    def submit(name):
        page.get_by_role("button", name=name, exact=True).focus()
        page.keyboard.press("Enter")

    try:
        page.goto(live_server.url + form_url)
        destination = page.locator("#id_destination_kind").bounding_box()
        assert destination is not None and destination["height"] >= 44
        shot("refund-form", form_url, "#manual-payment-title")
        choices = page.locator('input[name="refund_ids"]')
        expect(choices).to_have_count(3)
        for index in (0, 1):
            choices.nth(index).focus()
            page.keyboard.press("Space")
        page.get_by_label("مبلغ واریزشده (تومان)", exact=True).fill("399999")
        page.get_by_label("چهار رقم آخر مقصد", exact=True).fill("6037")
        page.get_by_label("شماره‌ی پیگیری بانک", exact=True).fill(reference)
        submit("ثبت واریز بازپرداخت")
        expect(page.locator(".errorlist").first).to_contain_text("مبلغ واریز باید دقیقاً برابر")
        expect(page.locator("#id_bank_ref")).to_have_value(reference)
        shot("refund-mismatch-long-reference", form_url, "#manual-payment-title")
        page.locator("#id_amount").fill("400000")
        submit("ثبت واریز بازپرداخت")
        page.wait_for_url(live_server.url + detail)
        expect(page.locator("#refund-history-title")).to_be_visible()
        shot("refund-recorded", detail, "#refund-history-title")
        page.goto(live_server.url + report)
        expect(page.locator(".order-reference-warning").first).to_be_visible()
        shot("reconciliation-long-reference", report, "#reconciliation-title")
        page.get_by_label("روز (شمسی)", exact=True).fill("1400/01/01")
        submit("نمایش روز")
        expect(
            page.get_by_text("بازپرداختی در این روز ثبت یا واریز نشده است.", exact=True)
        ).to_be_visible()
        shot("reconciliation-empty", report, "#reconciliation-title")
        page.get_by_label("روز (شمسی)", exact=True).fill("1405/13/32")
        submit("نمایش روز")
        expect(page.locator(".errorlist")).to_be_visible()
        shot("reconciliation-invalid-day", report, "#reconciliation-title")
        execute(order, refunds[2:], operator)
        page.goto(live_server.url + form_url)
        expect(page.get_by_text("بازپرداختی در انتظار واریز نیست.", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="ثبت واریز بازپرداخت", exact=True)).to_have_count(0)
        shot("refund-none-owed", form_url, "#manual-payment-title")
    finally:
        context.close()

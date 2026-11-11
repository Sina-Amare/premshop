import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.core.formatting import format_toman
from apps.orders.tests.test_delivery import queued
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import REASON
from apps.orders.tests.test_replacement_browser import keyboard_page
from apps.payments.models import Refund

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_operator_cancels_a_queued_item_and_sees_the_owed_refund_with_the_keyboard_alone(
    browser, live_server, buyer, plan, operator, width
):
    order, item = queued(buyer, plan, operator)
    context, page = keyboard_page(browser, live_server, operator, width)
    detail = live_server.url + reverse("admin:orders_order_change", args=[order.pk])

    def fits():
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

    try:
        page.goto(detail)
        page.get_by_role("link", name="تحویل", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", name="لغو و بازپرداخت")).to_be_visible()
        expect(page.get_by_text(f"مبلغ بازپرداخت: {format_toman(200000)}")).to_be_visible()
        fits()
        page.get_by_label("توضیح لغو", exact=True).focus()
        page.keyboard.type(REASON)
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        expect(page.get_by_text(f"{format_toman(200000)}، در انتظار واریز")).to_be_visible()
        assert page.url == detail
        fits()
        assert Refund.objects.get().order_item_id == item.pk
    finally:
        context.close()

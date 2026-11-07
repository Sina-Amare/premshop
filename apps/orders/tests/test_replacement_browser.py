import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.orders.models import Delivery, OrderItem
from apps.orders.tests.test_delivery import queued
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_replacement import CLAIM, DELAY, NEW_VALUE, REJECTION, delivered

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def keyboard_page(browser, live_server, operator, width):
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
            }
        ]
    )
    return context, context.new_page()


@pytest.mark.parametrize("width", [390, 1440])
def test_operator_replaces_then_rejects_a_claim_with_the_keyboard_alone(
    browser, live_server, buyer, plan, operator, width
):
    order, item = delivered(buyer, plan, operator)
    context, page = keyboard_page(browser, live_server, operator, width)
    detail = live_server.url + reverse("admin:orders_order_change", args=[order.pk])
    deliver = reverse("admin:orders_order_deliver", args=[order.pk, item.pk])

    def fits():
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

    def open_claim(note):
        page.goto(detail)
        page.get_by_role("link", name="درخواست تعویض", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.get_by_label("دلیل درخواست تعویض", exact=True)).to_be_visible()
        assert page.url.endswith(deliver)
        fits()
        page.get_by_label("دلیل درخواست تعویض", exact=True).focus()
        page.keyboard.type(note)
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        expect(page.get_by_text(note)).to_be_visible()
        fits()

    try:
        open_claim(CLAIM)
        page.get_by_label("عنوان ۱", exact=True).focus()
        page.keyboard.type("رمز عبور")
        page.keyboard.press("Tab")
        page.keyboard.type(NEW_VALUE)
        page.keyboard.press("Enter")
        expect(page.locator("[data-delivery-card]")).to_contain_text(NEW_VALUE)
        fits()
        page.get_by_role("button", name="ثبت تعویض", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("link", name="درخواست تعویض", exact=True)).to_be_visible()
        assert NEW_VALUE not in page.content()

        open_claim("دوباره مسدود شد")
        page.get_by_label("دلیل رد درخواست", exact=True).focus()
        page.keyboard.type(REJECTION)
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        expect(page.get_by_role("link", name="درخواست تعویض", exact=True)).to_be_visible()
        expect(page.get_by_text(REJECTION)).to_be_visible()
        fits()
    finally:
        context.close()
    item.refresh_from_db()
    assert item.status == "DELIVERED" and Delivery.objects.filter(order_item=item).count() == 2


@pytest.mark.parametrize("width", [390, 1440])
def test_operator_records_a_supply_delay_with_the_keyboard_alone(
    browser, live_server, buyer, plan, operator, width
):
    order, item = queued(buyer, plan, operator)
    due = item.due_at
    context, page = keyboard_page(browser, live_server, operator, width)
    try:
        page.goto(live_server.url + reverse("admin:orders_order_deliver", args=[order.pk, item.pk]))
        page.get_by_label("چند ساعت دیرتر (۱ تا ۷۲)", exact=True).focus()
        page.keyboard.type("۲۴")
        page.keyboard.press("Tab")
        page.keyboard.type(DELAY)
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        expect(page.get_by_label("چند ساعت دیرتر (۱ تا ۷۲)", exact=True)).to_be_empty()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    finally:
        context.close()
    item.refresh_from_db()
    assert item.due_at > due and OrderItem.objects.get(pk=item.pk).status == "QUEUED"

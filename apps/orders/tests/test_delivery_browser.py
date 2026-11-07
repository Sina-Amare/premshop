import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.orders.models import Delivery
from apps.orders.tests.test_delivery import VALUE, queued
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_operator_delivers_with_the_keyboard_alone(
    browser, live_server, buyer, plan, operator, width
):
    order, item = queued(buyer, plan, operator)
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
    page = context.new_page()
    detail = reverse("admin:orders_order_change", args=[order.pk])
    deliver = reverse("admin:orders_order_deliver", args=[order.pk, item.pk])

    def fits():
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

    try:
        page.goto(live_server.url + detail)
        page.get_by_role("link", name="تحویل", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", name="مشتری", exact=True)).to_be_visible()
        assert page.url.endswith(deliver)
        fits()
        page.get_by_label("عنوان ۱", exact=True).focus()
        page.keyboard.type("رمز عبور")
        page.keyboard.press("Tab")
        page.keyboard.type(VALUE)
        page.keyboard.press("Enter")
        card = page.locator("[data-delivery-card]")
        expect(card).to_contain_text(VALUE)
        fits()
        confirm = page.get_by_role("button", name="ثبت تحویل", exact=True)
        confirm.focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("button", name="نمایش اطلاعات تحویل", exact=True)).to_be_visible()
        assert page.url.endswith(detail)
        assert VALUE not in page.content()
        fits()
        page.get_by_role("button", name="نمایش اطلاعات تحویل", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.locator("[data-delivery-card]")).to_contain_text(VALUE)
        fits()
    finally:
        context.close()
    assert Delivery.objects.get().order_item_id == item.pk

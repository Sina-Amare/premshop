from datetime import timedelta

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.accounts.models import User
from apps.orders.tests.test_queue_admin import make_order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_a_holiday_from_start_to_end_by_keyboard(browser, live_server, buyer, plan, width):
    owner = User.objects.create_superuser("holiday-browser@example.test")
    client = Client()
    client.force_login(owner)
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
    holiday_page = live_server.url + reverse("admin:orders_order_holiday")
    order = make_order(buyer, plan, due_at=timezone.now() + timedelta(hours=3))
    item = order.items.get()

    def check(url, landmark):
        page.wait_for_url(lambda current: current.startswith(url))
        expect(page.get_by_text(landmark, exact=False).first).to_be_visible()
        page.evaluate("document.fonts.ready")
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

    def press(name):
        button = page.get_by_role("button", name=name, exact=True)
        button.focus()
        page.keyboard.press("Enter")

    try:
        page.goto(holiday_page)
        check(holiday_page, "تحویل هر روز")
        expect(page.locator("[data-holiday-banner]")).to_have_count(0)
        press("شروع تعطیلی")
        check(holiday_page, "از همین لحظه")
        press("تأیید و شروع تعطیلی")
        check(holiday_page, "در تعطیلی")
        expect(page.locator("[data-holiday-banner]")).to_be_visible()
        queue = live_server.url + reverse("admin:orders_order_changelist")
        page.goto(queue)
        check(queue, "متوقف در تعطیلی")
        expect(page.locator("[data-holiday-banner]")).to_be_visible()
        deliver = live_server.url + reverse("admin:orders_order_deliver", args=[order.pk, item.pk])
        page.goto(deliver)
        check(deliver, "متوقف در تعطیلی")
        expect(page.get_by_role("button", name="پیش‌نمایش")).to_be_visible()
        page.get_by_role("link", name="مدیریت تعطیلی").focus()
        page.keyboard.press("Enter")
        check(holiday_page, "زودترین مهلت جدید")
        press("پایان تعطیلی")
        press("تأیید و پایان تعطیلی")
        check(holiday_page, "تعطیلی‌های گذشته")
        expect(page.locator(".holiday-past li")).to_have_count(1)
        expect(page.locator("[data-holiday-banner]")).to_have_count(0)
        assert not errors
    finally:
        context.close()

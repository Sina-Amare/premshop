from datetime import timedelta

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.accounts.models import User
from apps.core.formatting import to_persian_digits
from apps.orders.tests.test_queue_admin import make_order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("javascript", [True, False])
def test_queue_tabs_keyboard_search_and_delivery_shell(
    browser, live_server, buyer, plan, width, javascript, tmp_path
):
    operator = User.objects.create_superuser("queue-browser@example.test")
    client = Client()
    client.force_login(operator)
    context = browser.new_context(
        viewport={"width": width, "height": 1000}, java_script_enabled=javascript
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
    listing = reverse("admin:orders_order_changelist")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def check(name, landmark):
        assert page.url.startswith(live_server.url + listing)
        expect(page.get_by_text(landmark, exact=False).first).to_be_visible()
        page.evaluate("document.fonts.ready")
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        if javascript:
            page.screenshot(path=str(tmp_path / f"{name}-{width}.png"), full_page=True)

    try:
        page.goto(live_server.url + listing)
        check("empty", "در صف انجام")
        assert page.locator("#result_list tbody tr").count() == 0
        expect(page.get_by_text("سفارشی پیدا نکردیم.", exact=True)).to_be_visible()
        plan.title = "پلن یک ماهه با نام طولانی برای فعال‌سازی روی اکانت شخصی Example Pro"
        plan.save(update_fields=["title"])
        now = timezone.now()
        order = make_order(buyer, plan, due_at=now - timedelta(hours=1))
        make_order(buyer, plan, due_at=now + timedelta(hours=2))
        make_order(buyer, plan, "AWAITING_INPUT", input_requested_at=now)
        paused = make_order(
            buyer,
            plan,
            "AWAITING_INPUT",
            input_requested_at=now - timedelta(days=1),
            sla_paused_at=now - timedelta(days=1),
            due_at=now - timedelta(days=1) + timedelta(hours=2),
        )
        make_order(buyer, plan, "PENDING_PAYMENT")
        page.reload()
        check("queued", "گذشته از مهلت")
        assert page.locator("#result_list tbody tr").count() == 2
        waiting = page.get_by_role("navigation", name="صف سفارش‌ها").get_by_role(
            "link", name="منتظر اطلاعات مشتری"
        )
        waiting.focus()
        page.keyboard.press("Enter")
        check("waiting", "در انتظار از")
        expect(page.get_by_text("متوقف", exact=False).first).to_be_visible()
        expect(page.get_by_text("۰۲:۰۰:۰۰", exact=False).first).to_be_visible()
        assert page.locator('.order-queue-time[data-clock="overdue"]').count() == 0
        unpaid = page.get_by_role("navigation", name="صف سفارش‌ها").get_by_role(
            "link", name="منتظر پرداخت"
        )
        unpaid.focus()
        page.keyboard.press("Enter")
        check("unpaid", "مانده")
        page.locator("#searchbar").fill("پیدانمی‌شود")
        page.locator("#searchbar").press("Enter")
        check("no-results", "منتظر پرداخت")
        assert "tab=unpaid" in page.url
        assert page.locator("#result_list tbody tr").count() == 0
        page.get_by_role("link", name="پاک کردن جستجو", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.locator("#result_list tbody tr")).to_have_count(1)
        page.goto(live_server.url + listing)
        page.locator("#searchbar").fill(to_persian_digits(order.order_number))
        page.locator("#searchbar").press("Enter")
        expect(page.locator("#result_list tbody tr")).to_have_count(1)
        page.goto(live_server.url + reverse("admin:orders_order_change", args=[paused.pk]))
        check("detail-paused", "متوقف")
        expect(page.get_by_role("button", name="ثبت دریافت اطلاعات")).to_be_visible()
        paging_buyer = User.objects.create_user("queue-paging@example.test")
        for _ in range(26):
            make_order(paging_buyer, plan, due_at=now + timedelta(hours=3))
        page.goto(live_server.url + listing + "?tab=queued&q=queue-paging")
        expect(page.locator("#result_list tbody tr")).to_have_count(25)
        expect(
            page.get_by_role("navigation", name="صفحه‌های سفارش‌ها").get_by_role(
                "link", name="۲", exact=True
            )
        ).to_be_visible()
        page.locator('a[href*="p=2"]').first.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#result_list tbody tr")).to_have_count(1)
        assert "tab=queued" in page.url and "q=queue-paging" in page.url
        expect(page.locator('.order-queue-pagination [aria-current="page"]')).to_have_text("۲")
        check("pagination", "در صف انجام")
        assert not errors
    finally:
        context.close()

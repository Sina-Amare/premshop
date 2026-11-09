from copy import deepcopy

import pytest

from apps.accounts.models import User
from apps.core.email import render_email
from apps.core.views import EMAIL_PREVIEWS
from apps.notifications.models import Notification
from apps.notifications.services import discover_confirmations
from apps.notifications.tests.test_confirmation import another_order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_confirmation_long_content_and_panel_states(
    browser, live_server, client, plan, width, tmp_path
):
    from playwright.sync_api import expect

    data = deepcopy(EMAIL_PREVIEWS["purchase_confirmation"])
    data["policies"][0]["body"] = (
        "متن کامل شرایط خرید Example Pro و جزئیات سفارش.\n\n" * 40
    ) + "پایان شرایط"
    _, _, html = render_email("purchase_confirmation", data)
    context = browser.new_context(viewport={"width": width, "height": 1000})
    page = context.new_page()
    try:
        page.set_content(html)
        expect(page.locator("h1")).to_contain_text("ثبت شد")
        expect(page.get_by_text("پایان شرایط", exact=False)).to_be_visible()
        assert not page.locator("details").count()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"confirmation-long-{width}.png"), full_page=True)
        operator = User.objects.create_superuser("operator@example.test", "fixture-passphrase-792!")
        client.force_login(operator)
        context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": client.cookies["sessionid"].value,
                    "url": live_server.url,
                }
            ]
        )
        page.goto(live_server.url + "/admin/notifications/notification/")
        expect(page).to_have_url(live_server.url + "/admin/notifications/notification/")
        expect(page.get_by_role("heading", name="چیزی پیدا نکردیم")).to_be_visible()
        page.screenshot(
            path=str(tmp_path / f"confirmation-panel-empty-{width}.png"), full_page=True
        )
        another_order(plan, 1)
        another_order(plan, 2)
        discover_confirmations()
        Notification.objects.filter(pk=Notification.objects.last().pk).update(
            status="failed", next_attempt_at=None, attempts=6
        )
        page.reload()
        expect(page.locator("#result_list")).to_contain_text("در انتظار")
        expect(page.locator("#result_list")).to_contain_text("ناموفق")
        assert not page.locator('input[name="_selected_action"]').count()
        page.screenshot(
            path=str(tmp_path / f"confirmation-panel-states-{width}.png"), full_page=True
        )
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_order_email_previews(browser, live_server, settings, monkeypatch, width, tmp_path):
    from playwright.sync_api import expect

    settings.DEBUG = True
    context = browser.new_context(viewport={"width": width, "height": 1000})
    page = context.new_page()
    headings = {
        "payment_confirmed": "پرداخت تأیید شد",
        "input_requested": "منتظر اطلاعات شما هستیم",
        "delivery_completed": "محصول تحویل شد",
        "replacement_rejected": "درخواست تعویض رد شد",
        "supply_delayed": "مهلت تحویل تغییر کرد",
        "item_cancelled": "محصول لغو شد",
        "item_refunded": "بازپرداخت واریز شد",
    }
    try:
        for name, heading in headings.items():
            for state in ("normal", "long"):
                data = dict(EMAIL_PREVIEWS[name])
                if state == "long":
                    if "product_name" in data:
                        data["product_name"] = "نام بلند محصول Example Pro " * 15
                    data["amount"] = 999999999999
                monkeypatch.setitem(EMAIL_PREVIEWS, name, data)
                url = f"{live_server.url}/dev/emails/{name}/"
                page.goto(url)
                expect(page).to_have_url(url)
                expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
                expect(page.get_by_role("link", name="مشاهده‌ی سفارش")).to_have_attribute(
                    "href", str(data["order_url"])
                )
                assert page.locator("html").get_attribute("dir") == "rtl"
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(tmp_path / f"{name}-{state}-{width}.png"), full_page=True)
        monkeypatch.setitem(
            EMAIL_PREVIEWS,
            "item_cancelled",
            {
                **EMAIL_PREVIEWS["item_cancelled"],
                "amount": 0,
            },
        )
        page.goto(f"{live_server.url}/dev/emails/item_cancelled/")
        expect(page.get_by_text("مبلغی برای بازپرداخت این محصول ثبت نشده است.")).to_be_visible()
        page.screenshot(path=str(tmp_path / f"cancelled-zero-{width}.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_notification_panel_types_and_states(
    browser, live_server, client, buyer, plan, width, tmp_path
):
    from playwright.sync_api import expect

    operator = User.objects.create_superuser("operator@example.test", "fixture-passphrase-792!")
    client.force_login(operator)
    context = browser.new_context(viewport={"width": width, "height": 1000})
    context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page = context.new_page()
    try:
        panel = live_server.url + "/admin/notifications/notification/"
        page.goto(panel)
        expect(
            page.locator('#nav-sidebar-inner a[href="/admin/notifications/notification/"]')
        ).to_contain_text("پیام‌های سفارش")
        expect(page.get_by_role("heading", name="چیزی پیدا نکردیم")).to_be_visible()
        page.screenshot(path=str(tmp_path / f"messages-empty-{width}.png"), full_page=True)
        from apps.orders.tests.test_delivery import queued

        order, _ = queued(buyer, plan, operator)
        discover_confirmations()
        Notification.objects.filter(template_name="payment_confirmed").update(
            status="failed",
            attempts=1,
            next_attempt_at=None,
            last_error_class="SMTPDataError",
            smtp_code=550,
        )
        page.reload()
        expect(page.locator("#result_list")).to_contain_text("تأیید پرداخت")
        expect(page.locator("#result_list")).to_contain_text("تأییدیه‌ی خرید")
        expect(page.locator("#result_list")).to_contain_text("ناموفق")
        assert not page.locator('input[name="_selected_action"]').count()
        page.screenshot(path=str(tmp_path / f"messages-mixed-{width}.png"), full_page=True)
        filtered = panel + "?template_name__exact=payment_confirmed"
        page.goto(filtered)
        expect(page).to_have_url(filtered)
        expect(page.locator("#result_list tbody tr")).to_have_count(1)
        expect(page.locator("#result_list")).to_contain_text("تأیید پرداخت")
        page.screenshot(path=str(tmp_path / f"messages-filtered-{width}.png"), full_page=True)
        row = Notification.objects.get(order=order, template_name="payment_confirmed")
        page.goto(panel + f"{row.pk}/change/")
        expect(page.locator("#content")).to_contain_text("SMTPDataError")
        assert not page.locator('input[name="_save"]').count()
        assert not page.locator("a.deletelink").count()
        page.screenshot(path=str(tmp_path / f"message-readonly-{width}.png"), full_page=True)
    finally:
        context.close()

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

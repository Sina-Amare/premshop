from datetime import UTC, datetime

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.accounts.models import User
from apps.core.email_log import fingerprint
from apps.core.formatting import format_jalali
from apps.core.models import MailAttempt
from apps.orders.tests.test_customer_browser import context_for

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("state", ["empty", "warning", "reserve", "exhausted", "unavailable"])
def test_mail_panel_states_and_private_address_search(
    browser, live_server, settings, width, state, tmp_path
):
    settings.EMAIL_QUOTA_RESET_DATE = datetime.now(UTC).date().replace(day=1).isoformat()
    settings.EMAIL_QUOTA_STARTING_USAGE = {
        "empty": 0,
        "warning": 800,
        "reserve": 900,
        "exhausted": 1000,
        "unavailable": 0,
    }[state]
    if state == "unavailable":
        settings.EMAIL_QUOTA_RESET_DATE = ""
    if state != "empty":
        for status in MailAttempt.Status:
            MailAttempt.objects.create(
                template="password_reset",
                recipient_hash=fingerprint("buyer@example.test"),
                status=status,
                error_type="SMTPServerDisconnected" if status == "uncertain" else "",
            )
    operator = User.objects.create_superuser("mail-owner@example.test")
    context = context_for(browser, live_server, operator, width)
    page = context.new_page()
    path = reverse("admin:core_mailattempt_changelist")
    try:
        page.goto(live_server.url + path)
        expect(page).to_have_url(live_server.url + path)
        expect(page.locator("#searchbar")).to_be_visible()
        if state == "empty":
            expect(page.locator("[data-mail-quota]")).to_have_count(0)
        else:
            expect(page.locator("[data-mail-quota]")).to_be_visible()
            expect(page.get_by_text("کد بازیابی رمز عبور", exact=True).first).to_be_visible()
            expect(page.locator(".field-requested_at").first).to_contain_text(
                format_jalali(datetime.now(UTC))
            )
            assert page.locator(".field-status_pill").first.evaluate("el => el.clientWidth >= 140")
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(tmp_path / f"mail-{state}-{width}.png"), full_page=True)
        page.locator("#searchbar").fill("buyer@example.test")
        page.locator("#changelist-search button").click()
        expect(page).to_have_url(live_server.url + path + "?q=" + fingerprint("buyer@example.test"))
        assert "buyer@example.test" not in page.content()
    finally:
        context.close()

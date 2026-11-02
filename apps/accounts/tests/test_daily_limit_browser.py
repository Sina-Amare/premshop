import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.core import ratelimit

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("network", [False, True])
def test_daily_refusal_is_readable_in_both_code_flows(
    browser, live_server, client, width, recovery, network, tmp_path
):
    from playwright.sync_api import expect

    email = "daily-ui@example.test"
    limit = (
        ratelimit.OTP_REQUESTS_PER_IP_DAILY if network else ratelimit.OTP_REQUESTS_PER_EMAIL_DAILY
    )
    subject = "127.0.0.1" if network else email
    for _ in range(limit.times):
        ratelimit.hit(limit, subject)
    context = browser.new_context(viewport={"width": width, "height": 1000})
    if recovery:
        client.force_login(User.objects.create_user(email))
        context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": client.cookies["sessionid"].value,
                    "url": live_server.url,
                }
            ]
        )
    page = context.new_page()
    path = reverse("account-password-recovery" if recovery else "login-code")
    try:
        page.goto(live_server.url + path)
        if recovery:
            page.get_by_role("button", name="فرستادن کد جدید").click()
        else:
            page.locator('input[name="email"]').fill(email)
            page.get_by_role("button", name="دریافت کد ورود").click()
        expect(page).to_have_url(live_server.url + path)
        text = (
            "درخواست کد از این شبکه به سقف روزانه رسیده است."
            if network
            else "درخواست کد برای این ایمیل به سقف روزانه رسیده است."
        )
        expect(page.get_by_text(text, exact=False)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(
            path=str(tmp_path / f"daily-{recovery}-{network}-{width}.png"), full_page=True
        )
    finally:
        context.close()

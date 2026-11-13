import pytest
from django.urls import reverse

from apps.core import ratelimit

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("network", [False, True])
def test_daily_refusal_is_readable_on_the_code_page(browser, live_server, width, network, tmp_path):
    from playwright.sync_api import expect

    phone = "09121230005"
    limit = (
        ratelimit.OTP_REQUESTS_PER_IP_DAILY if network else ratelimit.OTP_REQUESTS_PER_PHONE_DAILY
    )
    subject = "127.0.0.1" if network else phone
    for _ in range(limit.times):
        ratelimit.hit(limit, subject)
    context = browser.new_context(viewport={"width": width, "height": 1000})
    page = context.new_page()
    path = reverse("login-code")
    try:
        page.goto(live_server.url + path)
        page.locator('input[name="phone"]').fill(phone)
        page.get_by_role("button", name="دریافت کد ورود").click()
        expect(page).to_have_url(live_server.url + path)
        text = (
            "درخواست کد از این شبکه به سقف روزانه رسیده است."
            if network
            else "درخواست کد برای این شماره به سقف روزانه رسیده است."
        )
        expect(page.get_by_text(text, exact=False)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"daily-{network}-{width}.png"), full_page=True)
    finally:
        context.close()

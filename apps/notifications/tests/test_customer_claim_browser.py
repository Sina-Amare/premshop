import pytest
from playwright.sync_api import expect

from apps.core.views import EMAIL_PREVIEWS
from apps.orders.tests.test_customer_browser import capture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("long", [False, True])
def test_customer_claim_email_preview(
    browser, live_server, settings, monkeypatch, width, long, tmp_path
):
    settings.DEBUG = True
    if long:
        monkeypatch.setitem(
            EMAIL_PREVIEWS,
            "customer_claim",
            {**EMAIL_PREVIEWS["customer_claim"], "product_name": "اشتراک نمونه Example Pro " * 12},
        )
    context = browser.new_context(viewport={"width": width, "height": 1000})
    page = context.new_page()
    try:
        target = live_server.url + "/dev/emails/customer_claim/"
        page.goto(target)
        expect(page).to_have_url(target)
        expect(page.get_by_role("heading", name="درخواست تعویض مشتری", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="مشاهده‌ی سفارش در پنل")).to_have_attribute(
            "href", EMAIL_PREVIEWS["customer_claim"]["panel_url"]
        )
        capture(page, f"claim-email-{'long' if long else 'normal'}", width, tmp_path)
    finally:
        context.close()

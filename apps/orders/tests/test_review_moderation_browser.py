import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_review_moderation import HOME_LABEL, panel_url, review_for

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("width", [390, 1440])
def test_moderation_from_the_panel_home_through_history(
    browser, live_server, buyer, plan, operator, width, tmp_path
):
    client = Client()
    client.force_login(operator)
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
    queue = panel_url()
    home = reverse("admin:index")

    def check(name, url, locator):
        expect(page).to_have_url(live_server.url + url)
        expect(locator).to_be_visible()
        page.evaluate("document.fonts.ready")
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"{name}-{width}.png"), full_page=True)

    try:
        page.goto(live_server.url + home)
        home_tile = page.get_by_role("link", name=HOME_LABEL, exact=False)
        expect(home_tile).to_have_count(0)
        check("home-empty", home, page.get_by_role("heading", name="نیاز به رسیدگی"))
        page.goto(live_server.url + queue)
        check("queue-empty", queue, page.get_by_text("نظری در این فهرست نیست.", exact=True))

        product_name = "Netflix Pro 4K"
        plan.product.name = product_name
        plan.product.save(update_fields=["name"])
        row = review_for(buyer, plan, text=("همه‌چیز خوب بود. Sharp 4K " * 43)[:1000])
        detail = panel_url("change", row)
        page.goto(live_server.url + home)
        check("home-pending", home, home_tile)
        expect(home_tile).to_contain_text("۱")
        home_tile.press("Enter")
        link = page.get_by_role("link", name=product_name)
        check("queue-pending", queue, link)
        link.press("Enter")
        publish = page.get_by_role("button", name="انتشار نظر")
        check("detail-pending", detail, publish)
        assert (
            page.locator(".review-copy").evaluate("(node) => getComputedStyle(node).direction")
            == "rtl"
        )
        page.get_by_role("link", name="تاریخچه‌ی تغییرات").press("Enter")
        history = panel_url("history", row)
        check(
            "history-empty",
            history,
            page.get_by_text("هنوز تغییری برای این مورد ثبت نشده.", exact=False),
        )
        page.go_back()
        expect(publish).to_be_visible()
        stale = context.new_page()
        stale.goto(live_server.url + detail)
        publish.press("Enter")
        check("detail-published", detail, page.get_by_text("نظر منتشر شد.", exact=True))
        published_queue = queue + "?status=published"
        page.goto(live_server.url + published_queue)
        check("queue-published", published_queue, link)
        link.press("Enter")
        expect(page).to_have_url(live_server.url + detail)
        stale.get_by_role("button", name="انتشار نظر").press("Enter")
        expect(stale.get_by_text("وضعیت این نظر عوض شده است.")).to_be_visible()
        assert stale.url == live_server.url + detail
        stale.screenshot(path=str(tmp_path / f"detail-stale-{width}.png"), full_page=True)
        stale.close()

        reason = page.get_by_label("دلیل پنهان‌کردن نظر")
        reason.focus()
        reason.press("End")
        expect(reason).to_have_value("private_information")
        hide = page.get_by_role("button", name="پنهان‌کردن نظر")
        hide.press("Enter")
        check("detail-hidden", detail, page.get_by_text("نظر پنهان شد.", exact=True))
        expect(hide).to_have_count(0)
        page.get_by_role("link", name="تاریخچه‌ی تغییرات").press("Enter")
        check("history", history, page.get_by_text("اطلاعات شخصی یا رمز ورود", exact=False))
        assert page.locator(".panel-history-scroll").evaluate(
            "(node) => node.scrollWidth <= node.clientWidth + 1"
        )
        hidden_queue = queue + "?status=hidden"
        page.goto(live_server.url + hidden_queue)
        check("queue-hidden", hidden_queue, link)
    finally:
        context.close()

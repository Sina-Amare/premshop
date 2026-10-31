from io import BytesIO
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from PIL import Image
from playwright.sync_api import expect

from apps.orders.services import OrderLine, place_order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def context_for(browser, live_server, buyer, width, *, javascript=True):
    client = Client()
    client.force_login(buyer)
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
    return context


def capture(page, name, width):
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    folder = Path(settings.BASE_DIR) / "screenshots/customer-orders"
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{name}-{width}.png"), full_page=True)
    return folder


@pytest.mark.parametrize("width", [390, 1440])
def test_order_journey_invoice_png_tracking_and_whole_cancellation(
    browser, live_server, buyer, plan, width
):
    order = place_order(user=buyer, lines=[OrderLine(plan, 2)])
    context = context_for(browser, live_server, buyer, width)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(live_server.url + reverse("order-list"))
        expect(page.get_by_role("heading", name="سفارش‌ها", exact=True)).to_be_visible()
        folder = capture(page, "list", width)
        page.get_by_role("link", name="مشاهده‌ی سفارش", exact=True).click()
        expect(page).to_have_url(
            live_server.url + reverse("order-detail", args=[order.order_number])
        )
        expect(page.locator("main")).to_contain_text("تعداد: ۲")
        capture(page, "detail", width)
        page.get_by_role("link", name="مشاهده‌ی فاکتور").click()
        expect(page.get_by_role("heading", name="فاکتور سفارش", exact=True)).to_be_visible()
        capture(page, "invoice", width)
        with page.expect_download() as pending:
            page.get_by_role("button", name="دانلود تصویر فاکتور").click()
        download = pending.value
        target = folder / f"invoice-export-{width}.png"
        download.save_as(target)
        with Image.open(BytesIO(target.read_bytes())) as image:
            assert image.format == "PNG" and image.width == 1200 and image.height > 600
            assert not image.getexif()
            assert buyer.email not in repr(image.info) and order.tracking_token not in repr(
                image.info
            )
        expect(page.locator("[data-invoice-status]")).to_have_text("تصویر فاکتور آماده شد.")
        page.get_by_role("link", name="بازگشت به سفارش").click()
        page.locator("summary").filter(has_text="لغو سفارش").click()
        page.get_by_role("button", name="بله، لغو سفارش").click()
        expect(page.locator(".acct__main [role=status]")).to_contain_text("سفارش لغو شد.")
        capture(page, "cancelled", width)
        page.get_by_role("link", name="لینک پیگیری سفارش").click()
        expect(page.get_by_role("heading", name="پیگیری سفارش", exact=True)).to_be_visible()
        expect(page.locator("main")).to_contain_text("لغو شده")
        assert buyer.email not in page.content() and order.tracking_token not in page.content()
        capture(page, "tracking", width)
        assert not errors
    finally:
        context.close()


def test_invoice_remains_readable_without_javascript(browser, live_server, buyer, plan):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    context = context_for(browser, live_server, buyer, 390, javascript=False)
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("order-invoice", args=[order.order_number]))
        expect(page.get_by_role("heading", name="فاکتور سفارش", exact=True)).to_be_visible()
        expect(page.locator("[data-order-total]")).to_contain_text("۲۰۰,۰۰۰")
        expect(page.get_by_role("button", name="دانلود تصویر فاکتور")).to_be_hidden()
    finally:
        context.close()


def test_long_mixed_language_invoice_export_keeps_every_line_within_browser_limits(
    browser, live_server, buyer, plan
):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    context = context_for(browser, live_server, buyer, 390)
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("order-invoice", args=[order.order_number]))
        page.evaluate("""() => {
            const element = document.getElementById('invoice-data');
            const data = JSON.parse(element.textContent);
            data.lines = Array.from({length: 100}, (_, i) => ({...data.lines[0], name: 'نام محصول فارسی ChatGPT Plus ' .repeat(5), plan: 'VeryLongUnbrokenPlanIdentifier'.repeat(4) + i}));
            element.textContent = JSON.stringify(data);
        }""")
        with page.expect_download() as pending:
            page.get_by_role("button", name="دانلود تصویر فاکتور").click()
        with Image.open(pending.value.path()) as image:
            assert image.width > 300 and image.height > 5000
            assert image.width * image.height <= 16777216
            assert image.height < 32767
        expect(page.locator("[data-invoice-status]")).to_have_text("تصویر فاکتور آماده شد.")
    finally:
        context.close()


def test_font_failure_keeps_html_and_offers_an_export_retry(browser, live_server, buyer, plan):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    context = context_for(browser, live_server, buyer, 390)
    page = context.new_page()
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    page.route("**/static/fonts/**", lambda route: route.abort())
    try:
        page.goto(live_server.url + reverse("order-invoice", args=[order.order_number]))
        page.get_by_role("button", name="دانلود تصویر فاکتور").click()
        expect(page.locator("[data-invoice-status]")).to_contain_text("متن فاکتور در همین صفحه هست")
        expect(page.get_by_role("button", name="دانلود تصویر فاکتور")).to_be_enabled()
        expect(page.locator("[data-order-total]")).to_contain_text("۲۰۰,۰۰۰")
        assert not downloads
    finally:
        context.close()


def test_export_draws_each_mixed_order_line_status(browser, live_server, buyer, plan):
    from apps.orders.models import OrderItem

    order = place_order(user=buyer, lines=[OrderLine(plan, 2)])
    first, second = order.items.order_by("pk")
    OrderItem.objects.filter(pk=first.pk).update(status=OrderItem.Status.DELIVERED)
    OrderItem.objects.filter(pk=second.pk).update(
        status=OrderItem.Status.CANCELLED,
        cancel_reason=OrderItem.CancelReason.CUSTOMER_BEFORE_PAYMENT,
    )
    context = context_for(browser, live_server, buyer, 390)
    page = context.new_page()
    page.add_init_script("""(() => {
        window.invoiceTexts = [];
        const draw = CanvasRenderingContext2D.prototype.fillText;
        CanvasRenderingContext2D.prototype.fillText = function(text, ...args) {
            window.invoiceTexts.push(text);
            return draw.call(this, text, ...args);
        };
    })();""")
    try:
        page.goto(live_server.url + reverse("order-invoice", args=[order.order_number]))
        with page.expect_download():
            page.get_by_role("button", name="دانلود تصویر فاکتور").click()
        texts = page.evaluate("window.invoiceTexts")
        assert "وضعیت: تحویل داده شده" in texts
        assert "وضعیت: لغو شده" in texts
    finally:
        context.close()

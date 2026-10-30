from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Plan
from apps.orders.services import OrderLine, cancel_unpaid_item, place_order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_order_panel_empty_populated_and_long_history(browser, live_server, buyer, plan, width):
    operator = User.objects.create_superuser("operator@example.test")
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
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    directory = Path(settings.BASE_DIR) / "screenshots" / "orders-foundation"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        listing = reverse("admin:orders_order_changelist")
        page.goto(live_server.url + listing)
        assert page.url.endswith(listing)
        assert "سفارش‌ها" in page.locator("h1").first.inner_text()
        assert page.locator("#changelist").is_visible()
        page.screenshot(path=str(directory / f"empty-{width}.png"), full_page=True)
        plan.title = "پلن یک ماهه برای فعال‌سازی حساب شخصی با نام طولانی در سرویس Example Pro"
        plan.input_type = Plan.InputType.EMAIL
        plan.max_quantity = 1
        plan.save()
        other = Plan.objects.create(
            product=plan.product,
            title="پلن بدون ورودی",
            sale_price=200000,
            cost_price=100000,
            max_quantity=2,
            input_type=Plan.InputType.NONE,
            telegram_required=False,
        )
        order = place_order(
            user=buyer,
            lines=[OrderLine(plan, 1, "never-render-this@example.test"), OrderLine(other, 2)],
        )
        cancel_unpaid_item(
            item_id=order.items.first().pk,
            actor=operator,
            note="لغو به درخواست مشتری؛ بررسی نام محصول و شرایط خرید ثبت‌شده.",
        )
        detail = reverse("admin:orders_order_change", args=[order.pk])
        page.goto(live_server.url + detail)
        page.evaluate("document.fonts.ready")
        assert page.url.endswith(detail)
        assert "سابقه‌ی سفارش" in page.locator("body").inner_text()
        assert "سرویس نمونه" in page.locator("body").inner_text()
        assert "never-render-this@example.test" not in page.content()
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.evaluate("document.fonts.check('600 16px Estedad')")
        assert not page.locator('[name="_save"]').count()
        if width == 390:
            row = page.locator("#items-group .form-row").first
            assert row.evaluate("el => getComputedStyle(el).display") == "grid"
            for label in (
                "محصول و پلن زمان خرید",
                "قیمت فروش زمان خرید",
                "قیمت تأمین زمان خرید",
                "وضعیت",
            ):
                assert page.locator(".panel-cell-label", has_text=label).first.is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(directory / f"detail-{width}.png"), full_page=True)
        page.goto(live_server.url + listing)
        assert page.locator("#result_list").is_visible()
        cancelled = page.locator('.order-status[data-state="CANCELLED"]:visible').first
        assert cancelled.is_visible()
        assert cancelled.evaluate("el => getComputedStyle(el).borderTopStyle") == "dashed"
        page.screenshot(path=str(directory / f"list-{width}.png"), full_page=True)
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_discount_editor_errors_creation_and_read_only_usage(browser, live_server, plan, width):
    from playwright.sync_api import expect

    from apps.orders.models import DiscountCode

    operator = User.objects.create_superuser("operator@example.test")
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
    directory = Path(settings.BASE_DIR) / "screenshots" / "unpaid-lifecycle"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        url = reverse("admin:orders_discountcode_add")
        page.goto(live_server.url + url)
        assert page.url.endswith(url) and "کدهای تخفیف" in page.locator("h1").first.inner_text()
        assert page.locator("#discountcode_form").is_visible()
        page.locator("#id_code").fill("save")
        page.select_option("#id_kind", "fixed")
        page.select_option("#id_scope", "selected")
        page.locator("#id_value").fill("0")
        page.locator('[name="_save"]').click()
        expect(page.get_by_text("عددی بزرگ‌تر از صفر وارد کنید.", exact=True)).to_be_visible()
        expect(
            page.get_by_text("حداقل یک محصول برای این کد تخفیف انتخاب کنید.", exact=True)
        ).to_be_visible()
        text = page.locator("body").inner_text()
        assert "Choose" not in text and "Remove all" not in text
        assert page.locator('label[for="id_value"]').inner_text().startswith("میزان تخفیف")
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(directory / f"discount-errors-{width}.png"), full_page=True)
        page.locator("#id_value").fill("100")
        page.select_option("#id_products", [str(plan.product_id)])
        page.locator('[name="_save"]').click()
        page.wait_for_url("**/admin/orders/discountcode/")
        code = DiscountCode.objects.get(code="SAVE")
        assert code.scope == "selected" and list(code.products.all()) == [plan.product]
        page.goto(live_server.url + reverse("admin:orders_discountcode_change", args=[code.pk]))
        expect(page.locator("#id_code")).to_have_value("SAVE")
        assert not page.locator('[name="used_count"]').count()
        assert "خالی یعنی بدون محدودیت" in page.locator("body").inner_text()
        page.screenshot(path=str(directory / f"discount-saved-{width}.png"), full_page=True)
    finally:
        context.close()

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.core.formatting import to_persian_digits
from apps.orders.models import CredentialAccessLog, Order, OrderItem, OrderSupportIdentifier
from apps.orders.tests.test_customer_browser import capture, context_for

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_customer_support_copy_replacement_and_invoice_privacy(
    browser, live_server, buyer, plan, width, tmp_path, settings
):
    order = Order.objects.create(user=buyer, subtotal=200000, total_amount=200000)
    OrderSupportIdentifier.objects.create(order=order)
    OrderItem.objects.create(
        order=order,
        plan=plan,
        price_snapshot=200000,
        cost_snapshot=100000,
        product_snapshot={
            "name": "نام بسیار طولانی محصول برای بررسی شکست سطر در صفحه‌ی سفارش",
            "plan_title": "یک ماهه",
        },
        status="AWAITING_INPUT",
    )
    context = context_for(browser, live_server, buyer, width)
    context.add_init_script("""Object.defineProperty(navigator, 'clipboard', {
      value: {writeText: async text => { window.copiedSupport = text; }}
    });""")
    page = context.new_page()
    detail = reverse("order-detail", args=[order.order_number])
    try:
        page.goto(live_server.url + detail)
        # Details awaited: what to send sits in the card; support's record stays closed.
        expect(page.locator("#order-help > summary")).to_have_text("پشتیبانی این سفارش")
        expect(page.locator("#order-help")).not_to_have_attribute("open", "")
        expect(page.locator("#order-next #support-identifier")).to_be_visible()
        expect(page.locator("[data-support-instructions]")).to_have_count(1)
        expect(page.locator("[data-support-instructions]")).to_contain_text(
            "شماره‌ی سفارش و شناسه‌ی پشتیبانی را در تلگرام برای پشتیبانی بفرستید."
        )
        identifier = page.locator("#support-identifier")
        expect(identifier).to_have_text(order.support_identifier.grouped_token)
        assert identifier.get_attribute("dir") == "ltr"
        assert page.url.endswith(detail)
        capture(page, "support-customer", width, tmp_path)
        copy = page.get_by_role("button", name="کپی شماره‌ی سفارش و شناسه", exact=True)
        copy.focus()
        page.keyboard.press("Enter")
        expect(page.locator("[data-copy-status]")).to_have_text("کپی شد")
        expect(page.locator("[data-copy-status]")).to_have_attribute("aria-live", "polite")
        expect(page.locator("[data-copy-status]")).to_have_attribute("aria-atomic", "true")
        number = to_persian_digits(order.order_number)
        token = order.support_identifier.grouped_token
        assert page.evaluate("window.copiedSupport") == f"سفارش {number}، شناسه‌ی پشتیبانی {token}"
        page.evaluate(
            "() => { navigator.clipboard.writeText = async () => { throw new Error('denied'); }; }"
        )
        copy.click()
        expect(page.locator("[data-copy-status]")).to_have_text(
            "کپی انجام نشد؛ شناسه را انتخاب و کپی کنید."
        )
        assert page.evaluate("getSelection().toString()") == order.support_identifier.grouped_token
        capture(page, "support-copy-refused", width, tmp_path)
        page.locator("#order-help > summary").click()
        replace = page.get_by_text("شناسه را به کسی داده‌اید؟", exact=True)
        assert replace.bounding_box()["height"] >= 44
        replace.click()
        expect(
            page.get_by_text("شناسه‌ی قبلی از همان لحظه باطل می‌شود.", exact=False)
        ).to_be_visible()
        capture(page, "support-confirm", width, tmp_path)
        old = order.support_identifier.support_token
        page.get_by_role("button", name="ساخت شناسه‌ی جدید", exact=True).click()
        expect(page.locator("#support-identifier")).not_to_have_text(
            order.support_identifier.grouped_token
        )
        order.refresh_from_db()
        assert order.support_identifier.support_token != old
        capture(page, "support-replaced", width, tmp_path)
        page.goto(live_server.url + reverse("order-invoice", args=[order.order_number]))
        assert (
            order.support_identifier.grouped_token not in page.content()
            and order.support_identifier.support_token not in page.content()
        )
        page.evaluate("""() => {
          window.invoiceTexts = [];
          const fill = CanvasRenderingContext2D.prototype.fillText;
          CanvasRenderingContext2D.prototype.fillText = function(text, ...args) {
            window.invoiceTexts.push(text); return fill.call(this, text, ...args);
          };
        }""")
        with page.expect_download() as pending:
            page.get_by_role("button", name="دانلود تصویر فاکتور", exact=True).click()
        pending.value.save_as(tmp_path / "support-invoice.png")
        assert order.support_identifier.grouped_token not in repr(
            page.evaluate("window.invoiceTexts")
        )
        assert order.support_identifier.support_token not in repr(
            page.evaluate("window.invoiceTexts")
        )
        original_key = settings.FIELD_ENCRYPTION_KEY
        settings.FIELD_ENCRYPTION_KEY = ""
        try:
            page.goto(live_server.url + detail)
            expect(page.get_by_text("شناسه‌ی پشتیبانی در دسترس نیست.", exact=True)).to_be_visible()
            expect(
                page.locator("[data-copy-support], input[name=expected_support_token]")
            ).to_have_count(0)
            assert page.url.endswith(detail)
            capture(page, "support-unavailable", width, tmp_path)
        finally:
            settings.FIELD_ENCRYPTION_KEY = original_key
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("language", ["fa", "en"])
def test_operator_matches_in_existing_panel_without_javascript(
    browser, live_server, buyer, width, language, tmp_path
):
    from apps.accounts.models import User

    order = Order.objects.create(user=buyer, subtotal=0, total_amount=0)
    OrderSupportIdentifier.objects.create(order=order)
    operator = User.objects.create_superuser("operator@example.test")
    context = context_for(browser, live_server, operator, width, javascript=False)
    context.add_cookies([{"name": "django_language", "value": language, "url": live_server.url}])
    page = context.new_page()
    detail = reverse("admin:orders_order_change", args=[order.pk])
    match = reverse("admin:orders_order_support_match", args=[order.pk])
    label = "شناسه‌ی پشتیبانی سفارش" if language == "fa" else "Order support identifier"
    check = "بررسی شناسه" if language == "fa" else "Check identifier"
    back = "بازگشت به سفارش" if language == "fa" else "Back to order"
    mismatch = (
        "شناسه با این سفارش مطابقت ندارد."
        if language == "fa"
        else "The identifier does not match this order."
    )
    matched = (
        "شناسه با این سفارش مطابقت دارد."
        if language == "fa"
        else "The identifier matches this order."
    )
    try:
        page.goto(live_server.url + detail)
        expect(page.get_by_label(label, exact=True)).to_be_visible()
        assert order.support_identifier.support_token not in page.content()
        assert page.url.endswith(detail)
        capture(page, "support-panel-form", width, tmp_path)
        for support_token, state, message in (
            ("", "empty", mismatch),
            ("ABCD EFGH JK23", "mismatch", mismatch),
            (order.support_identifier.grouped_token, "match", matched),
        ):
            page.get_by_label(label, exact=True).fill(support_token)
            page.get_by_role("button", name=check, exact=True).focus()
            page.keyboard.press("Enter")
            expect(page.locator(".order-history [role=status]")).to_have_text(message)
            assert page.url.endswith(match)
            assert (
                order.support_identifier.support_token not in page.content()
                and order.support_identifier.grouped_token not in page.content()
            )
            capture(page, "support-panel-" + state, width, tmp_path)
            page.get_by_role("link", name=back, exact=True).click()
    finally:
        context.close()


@pytest.mark.parametrize("width", [390])
def test_enter_in_the_identifier_field_compares_instead_of_revealing_input(
    browser, live_server, buyer, plan, width
):
    from apps.accounts.models import User

    order = Order.objects.create(user=buyer, subtotal=200000, total_amount=200000)
    identifier = OrderSupportIdentifier.objects.create(order=order)
    OrderItem.objects.create(
        order=order,
        plan=plan,
        price_snapshot=200000,
        cost_snapshot=100000,
        product_snapshot={
            "name": "محصول",
            "plan_title": "یک ماهه",
            "requires_customer_input": True,
        },
        customer_input="private@example.test",
        status="QUEUED",
    )
    operator = User.objects.create_superuser("operator@example.test")
    context = context_for(browser, live_server, operator, width, javascript=False)
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("admin:orders_order_change", args=[order.pk]))
        field = page.get_by_label("شناسه‌ی پشتیبانی سفارش", exact=True)
        field.fill(identifier.grouped_token)
        field.press("Enter")
        expect(page.locator(".order-history [role=status]")).to_have_text(
            "شناسه با این سفارش مطابقت دارد."
        )
        assert page.url.endswith(reverse("admin:orders_order_support_match", args=[order.pk]))
        assert not CredentialAccessLog.objects.exists()
    finally:
        context.close()

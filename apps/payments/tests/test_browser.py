import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Plan
from apps.orders.models import Order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def guest_context(browser, live_server, buyer, plan, width, *, javascript=True):
    account = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=account, plan=plan)
    guest = Client()
    guest.post(reverse("cart-add"), {"plan": plan.pk})
    key = guest.cookies[settings.SESSION_COOKIE_NAME].value
    context = browser.new_context(
        viewport={"width": width, "height": 1000},
        java_script_enabled=javascript,
    )
    context.add_cookies(
        [{"name": settings.SESSION_COOKIE_NAME, "value": key, "url": live_server.url}]
    )
    return context, account, key


def screenshot(page, label, width, directory):
    assert page.url.endswith(reverse("checkout"))
    expect(page.get_by_role("heading", name="ثبت سفارش", exact=True)).to_be_visible()
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(directory / f"{label}-{width}.png"), full_page=True)


@pytest.mark.parametrize("javascript", [True, False])
def test_inline_invalid_fields_use_persian_errors_in_english_browser(
    browser, live_server, javascript, monkeypatch, mailoutbox
):
    monkeypatch.setattr("apps.accounts.otp.secrets.randbelow", lambda limit: 123456)
    context = browser.new_context(locale="en-US", java_script_enabled=javascript)
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("checkout"))
        page.get_by_label("ایمیل", exact=True).fill("invalid")
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        if javascript:
            expect(page.locator("[data-login-status]")).to_contain_text("این ایمیل معتبر نیست")
            page.get_by_label("ایمیل", exact=True).fill("buyer@example.test")
            page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
            expect(page.locator("[data-inline-code]")).to_be_visible()
            page.get_by_role("button", name="تأیید کد و ادامه").click()
            expect(page.locator("[data-login-status]")).to_contain_text("کد را وارد کنید")
        else:
            expect(page).to_have_url(live_server.url + reverse("login-code"))
            expect(page.locator(".field__error")).to_contain_text("این ایمیل معتبر نیست")
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_inline_signin_merge_discount_stale_confirmation_and_purchase(
    browser,
    live_server,
    buyer,
    plan,
    width,
    monkeypatch,
    mailoutbox,
    tmp_path,
):
    monkeypatch.setattr("apps.accounts.otp.secrets.randbelow", lambda limit: 123456)
    context, cart, guest_key = guest_context(browser, live_server, buyer, plan, width)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(live_server.url + reverse("checkout"))
        screenshot(page, "guest", width, tmp_path)
        page.get_by_label("ایمیل", exact=True).fill(buyer.email)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page.locator("[data-inline-code]")).to_be_visible()
        page.get_by_label("کد ورود", exact=True).fill("000000")
        page.get_by_role("button", name="تأیید کد و ادامه").click()
        expect(page.locator("[data-login-status]")).to_contain_text("کد ورود درست نیست")
        page.get_by_label("کد ورود", exact=True).fill("۱۲۳۴۵۶")
        page.get_by_role("button", name="تأیید کد و ادامه").click()
        expect(page.locator("#checkout-form")).to_be_visible()
        assert cart.items.get().quantity == 2
        cookies = {row["name"]: row["value"] for row in context.cookies()}
        assert cookies[settings.SESSION_COOKIE_NAME] != guest_key
        assert page.locator('[name="activation_consent"]').count() == 0
        screenshot(page, "review", width, tmp_path)
        page.get_by_label("کد تخفیف", exact=True).fill("WRONG")
        page.get_by_role("button", name="اعمال کد تخفیف").click()
        expect(page.locator("[data-checkout-error]")).to_contain_text("کد تخفیف معتبر نیست")
        assert not page.get_by_role("button", name="ثبت سفارش", exact=True).count()
        screenshot(page, "discount-error", width, tmp_path)
        page.get_by_role("button", name="حذف کد تخفیف").click()
        Plan.objects.filter(pk=plan.pk).update(sale_price=250000)
        page.get_by_role("button", name="ثبت سفارش", exact=True).click()
        expect(page.locator("[data-checkout-error]")).to_contain_text("تغییر کرده است")
        assert page.locator('[name="activation_consent"]').count() == 0
        expect(page.locator(".cart-summary__total")).to_contain_text("۵۰۰,۰۰۰")
        screenshot(page, "stale", width, tmp_path)
        page.get_by_role("button", name="ثبت سفارش", exact=True).focus()
        page.get_by_role("button", name="ثبت سفارش", exact=True).click()
        expect(page).to_have_url(
            live_server.url + reverse("order-detail", args=[Order.objects.get().order_number])
        )
        assert Order.objects.get().total_amount == 500000 and not cart.items.exists()
        assert not errors and len(mailoutbox) == 1
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_no_javascript_signin_returns_to_checkout_and_can_submit(
    browser, live_server, buyer, plan, width, monkeypatch, mailoutbox
):
    monkeypatch.setattr("apps.accounts.otp.secrets.randbelow", lambda limit: 123456)
    context, cart, _ = guest_context(browser, live_server, buyer, plan, width, javascript=False)
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("checkout"))
        page.get_by_label("ایمیل", exact=True).fill(buyer.email)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page).to_have_url(live_server.url + reverse("login-code-verify"))
        page.locator("#id_code").fill("123456")
        page.get_by_role("button", name="ورود", exact=True).click()
        expect(page).to_have_url(live_server.url + reverse("checkout"))
        assert cart.items.get().quantity == 2
        assert page.get_by_role("checkbox").count() == 0
        page.get_by_role("button", name="ثبت سفارش", exact=True).click()
        assert Order.objects.count() == 1 and not cart.items.exists()
        assert len(mailoutbox) == 1
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_long_checkout_without_consent_and_empty_cart_remain_readable(
    browser, live_server, buyer, plan, width, tmp_path
):
    from apps.catalog.models import Product

    Product.objects.filter(pk=plan.product_id).update(
        name="اشتراک سالانه ChatGPT Plus با امکانات بیشتر"
    )
    client = Client()
    client.force_login(buyer)
    cart = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=cart, plan=plan)
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
    try:
        page.goto(live_server.url + reverse("checkout"))
        assert page.get_by_role("checkbox").count() == 0
        note = page.get_by_text("قیمت و موجودی را هنگام ثبت سفارش").bounding_box()
        button = page.get_by_role("button", name="ثبت سفارش", exact=True).bounding_box()
        deadline = page.get_by_text("مهلت پرداخت پس از ثبت سفارش").bounding_box()
        assert button["y"] - (note["y"] + note["height"]) >= 12
        assert deadline["y"] - (button["y"] + button["height"]) >= 12
        screenshot(page, "long-review", width, tmp_path)
        cart.items.all().delete()
        page.reload()
        expect(page.get_by_text("سبد خرید شما خالی است.", exact=True)).to_be_visible()
        screenshot(page, "empty", width, tmp_path)
    finally:
        context.close()

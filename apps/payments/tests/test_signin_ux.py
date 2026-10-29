from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.accounts.services import RateLimited
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db(transaction=True)
PASSWORD = "PreviewPassword!2026"  # noqa: S105 — synthetic test credential.


def test_guest_review_shows_selected_product_plan_quantity_and_server_total(client, plan):
    client.post(reverse("cart-add"), {"plan": plan.pk, "quantity": 2})
    response = client.get(reverse("checkout"))
    body = response.content.decode()
    assert "data-checkout-cart" in body
    assert plan.product.name in body and plan.title in body
    assert "تعداد: ۲" in body and "۴۰۰,۰۰۰" in body
    assert "پس از ورود، سبد خرید و مبلغ نهایی را بررسی می‌کنید" not in body
    assert "قیمت و موجودی هنگام ثبت سفارش دوباره بررسی می‌شوند" not in body


def test_guest_review_hides_drafts_and_excludes_unavailable_amounts(client, plan):
    client.post(reverse("cart-add"), {"plan": plan.pk})
    Product.objects.filter(pk=plan.product_id).update(status=Product.Status.DRAFT)
    body = client.get(reverse("checkout")).content.decode()
    assert "data-checkout-cart" in body and "محصول ناموجود" in body
    assert plan.product.name not in body and plan.title not in body and "۲۰۰,۰۰۰" not in body


def test_empty_guest_review_has_an_explicit_empty_state(client):
    body = client.get(reverse("checkout")).content.decode()
    assert "data-checkout-cart" in body and "سبد خرید شما خالی است" in body
    assert "۰ محصول" not in body


def test_already_signed_in_password_response_uses_safe_json_destination(client, buyer):
    client.force_login(buyer)
    response = client.post(reverse("login"), {"next": "/checkout/"}, HTTP_ACCEPT="application/json")
    assert response.json() == {"ok": True, "next": "/checkout/"}


@pytest.mark.parametrize("destination", ["/checkout/", "https://outside.example/"])
def test_inline_password_response_reuses_login_merge_and_safe_destination(
    client, buyer, plan, destination
):
    buyer.set_password(PASSWORD)
    buyer.save(update_fields=["password"])
    account = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=account, plan=plan)
    client.post(reverse("cart-add"), {"plan": plan.pk})
    before = client.session.session_key
    response = client.post(
        reverse("login"),
        {"email": buyer.email, "password": PASSWORD, "next": destination},
        HTTP_ACCEPT="application/json",
    )
    assert response.status_code == 200 and response.json() == {
        "ok": True,
        "next": "/checkout/" if destination == "/checkout/" else "/account/",
    }
    assert client.session.session_key != before and client.session["_auth_user_id"] == str(buyer.pk)
    assert account.items.get().quantity == 2


@pytest.mark.parametrize("failure", ["wrong", "inactive", "limit", "empty"])
def test_inline_password_failures_keep_guest_cart_and_return_plain_errors(
    client, buyer, plan, monkeypatch, failure
):
    buyer.set_password(PASSWORD)
    buyer.is_active = failure != "inactive"
    buyer.save(update_fields=["password", "is_active"])
    client.post(reverse("cart-add"), {"plan": plan.pk})
    before = client.session.session_key
    if failure == "limit":

        def limited(*args, **kwargs):
            raise RateLimited("تعداد تلاش‌ها زیاد است.")

        monkeypatch.setattr("apps.accounts.services.login_with_password", limited)
    response = client.post(
        reverse("login"),
        {
            "email": buyer.email,
            "password": "" if failure == "empty" else "wrong" if failure == "wrong" else PASSWORD,
        },
        HTTP_ACCEPT="application/json",
    )
    assert response.status_code == (429 if failure == "limit" else 400)
    assert response.json()["ok"] is False and response.json()["error"]
    assert PASSWORD not in response.content.decode()
    assert "_auth_user_id" not in client.session and client.session.session_key == before
    assert CartItem.objects.get().quantity == 1


@pytest.mark.browser
@pytest.mark.parametrize("authenticated", [False, True])
def test_cart_polling_never_replaces_checkout_or_discards_typed_values(
    browser, live_server, buyer, plan, authenticated
):
    client = Client()
    if authenticated:
        client.force_login(buyer)
    client.post(reverse("cart-add"), {"plan": plan.pk})
    context = browser.new_context()
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
    polls = []
    page.on(
        "request",
        lambda request: polls.append(request.url) if "/cart/availability/" in request.url else None,
    )
    try:
        page.clock.install()
        page.goto(live_server.url + reverse("checkout"))
        summary = page.locator(".cart-summary")
        expect(summary.get_by_role("link", name="ویرایش سبد خرید", exact=True)).to_be_visible()
        expect(page.locator(".cart-heading a")).to_have_count(0)
        field = (
            page.locator('[name="phone"]')
            if authenticated
            else page.get_by_label("ایمیل", exact=True)
        )
        value = "09121234567" if authenticated else "typed@example.test"
        field.fill(value)
        page.clock.fast_forward(16000)
        page.evaluate('document.dispatchEvent(new Event("visibilitychange"))')
        page.wait_for_timeout(500)
        expect(page.get_by_role("heading", name="ثبت سفارش", exact=True)).to_be_visible()
        expect(field).to_have_value(value)
        assert not polls
    finally:
        context.close()


@pytest.mark.browser
def test_switching_signin_method_keeps_requested_code_without_sending_again(
    browser, live_server, buyer, plan, monkeypatch, mailoutbox
):
    monkeypatch.setattr("apps.accounts.otp.secrets.randbelow", lambda limit: 123456)
    client = Client()
    client.post(reverse("cart-add"), {"plan": plan.pk})
    context = browser.new_context()
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.session.session_key,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("checkout"))
        page.get_by_label("ایمیل", exact=True).fill(buyer.email)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page.locator("[data-inline-code]")).to_be_visible()
        page.get_by_role("link", name="ورود با رمز عبور", exact=True).click()
        expect(page.locator("[data-inline-code]")).not_to_be_visible()
        page.get_by_role("link", name="ورود با کد ایمیل", exact=True).click()
        expect(page.locator("[data-inline-code]")).to_be_visible()
        expect(page.locator("[data-checkout-cart]")).to_contain_text(plan.product.name)
        expect(page.locator("[data-inline-resend] button")).to_be_disabled()
        assert len(mailoutbox) == 1
        page.get_by_label("کد ورود", exact=True).fill("123456")
        page.get_by_role("button", name="تأیید کد و ادامه", exact=True).click()
        expect(page.locator("#checkout-form")).to_be_visible()
        assert len(mailoutbox) == 1
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.parametrize("javascript", [True, False])
def test_password_choice_keeps_checkout_preview_and_rotates_merged_session(
    browser, live_server, buyer, plan, javascript
):
    buyer.set_password(PASSWORD)
    buyer.save(update_fields=["password"])
    account = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=account, plan=plan)
    client = Client()
    client.post(reverse("cart-add"), {"plan": plan.pk})
    key = client.session.session_key
    context = browser.new_context(
        java_script_enabled=javascript, viewport={"width": 390, "height": 1000}
    )
    context.add_cookies(
        [{"name": settings.SESSION_COOKIE_NAME, "value": key, "url": live_server.url}]
    )
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("checkout"))
        page.get_by_role("link", name="ورود با رمز عبور", exact=True).click()
        expect(page.locator("[data-checkout-login]")).to_be_visible()
        expect(page.locator("[data-checkout-cart]")).to_contain_text(plan.product.name)
        form = page.locator("[data-inline-email]")
        form.get_by_label("ایمیل", exact=True).fill(buyer.email)
        if javascript:
            form.get_by_label("رمز عبور", exact=True).fill("wrong")
            form.get_by_role("button", name="ورود", exact=True).click()
            expect(page.locator("[data-login-status]")).to_contain_text(
                "ایمیل یا رمز عبور اشتباه است"
            )
            page.get_by_role("button", name="نمایش رمز عبور", exact=True).click()
            expect(form.get_by_label("رمز عبور", exact=True)).to_have_attribute("type", "text")
            code_link = page.get_by_role("link", name="ورود با کد ایمیل", exact=True)
            code_link.focus()
            page.keyboard.press("Enter")
            expect(form.get_by_label("ایمیل", exact=True)).to_have_value(buyer.email)
            expect(form.get_by_label("رمز عبور", exact=True)).not_to_be_visible()
            page.get_by_role("link", name="ورود با رمز عبور", exact=True).click()
            expect(form.get_by_label("رمز عبور", exact=True)).to_have_value("")
            expect(form.get_by_label("رمز عبور", exact=True)).to_have_attribute("type", "password")
        form.get_by_label("رمز عبور", exact=True).fill(PASSWORD)
        form.get_by_role("button", name="ورود", exact=True).click()
        expect(page.locator("#checkout-form")).to_be_visible()
        assert account.items.get().quantity == 2
        cookies = {row["name"]: row["value"] for row in context.cookies()}
        assert cookies[settings.SESSION_COOKIE_NAME] != key
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.parametrize("width", [390, 1440])
def test_long_mixed_cart_preview_keeps_controls_and_prices_within_viewport(
    browser, live_server, plan, width
):
    plan.product.name = "اشتراک ابزار برنامه‌نویسی GitHub Copilot Enterprise برای تیم‌های بزرگ"
    plan.product.save(update_fields=["name"])
    plan.title = "یک‌ساله؛ " + "LongUnbrokenPlanName" * 4
    plan.sale_price = 999999999999
    plan.save(update_fields=["title", "sale_price"])
    client = Client()
    client.post(reverse("cart-add"), {"plan": plan.pk, "quantity": 2})
    context = browser.new_context(viewport={"width": width, "height": 1000})
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.session.session_key,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("checkout"))
        expect(page.locator("[data-checkout-cart]")).to_contain_text(plan.title)
        expect(page.get_by_role("link", name="ورود با رمز عبور", exact=True)).to_be_visible()
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        directory = Path(settings.BASE_DIR) / "screenshots" / "checkout-signin"
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(directory / f"long-{width}.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.parametrize("method", ["code", "password"])
def test_inline_signin_disables_the_visible_submit_while_request_is_pending(
    browser, live_server, method
):
    context = browser.new_context()
    page = context.new_page()
    held = []
    try:
        page.goto(live_server.url + reverse("checkout") + "?method=" + method)
        page.route(
            "**/login/" if method == "password" else "**/login/code/",
            lambda route: held.append(route),
        )
        form = page.locator("[data-inline-email]")
        form.get_by_label("ایمیل", exact=True).fill("buyer@example.test")
        if method == "password":
            form.get_by_label("رمز عبور", exact=True).fill(PASSWORD)
        submit = form.locator('[type="submit"]')
        submit.click()
        expect(form).to_have_attribute("aria-busy", "true")
        expect(submit).to_be_disabled()
        if method == "password":
            expect(page.get_by_role("button", name="نمایش رمز عبور", exact=True)).to_be_enabled()
        assert len(held) == 1
        held[0].fulfill(status=400, json={"ok": False, "error": "ایمیل یا رمز عبور اشتباه است."})
        expect(submit).to_be_enabled()
        expect(form).not_to_have_attribute("aria-busy", "true")
    finally:
        context.close()

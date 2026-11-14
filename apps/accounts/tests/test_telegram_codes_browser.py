"""Both code entry points keep the channel choice, cart and immediate SMS switch."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.accounts.tests.test_code_page_browser import PASTE_JS
from apps.accounts.tests.test_telegram_codes import PHONE, code_from, deliver
from apps.accounts.tests.test_telegram_codes import linked as linked
from apps.accounts.tests.test_telegram_codes import queued as queued
from apps.accounts.tests.test_telegram_codes import sent as sent
from apps.core import sms
from apps.core.tests.test_accessibility_browser import axe
from apps.orders.tests.conftest import plan as plan

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_sign_in_choice_error_and_immediate_sms_switch(
    browser, live_server, width, linked, queued, tmp_path
):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("login-code"))
        expect(page.get_by_role("heading", name="ورود با کد", exact=True)).to_be_visible()
        expect(page.get_by_label("پیامک", exact=True)).to_be_checked()
        page.screenshot(path=str(tmp_path / f"sign-in-{width}.png"), full_page=True)
        page.get_by_label("تلگرام", exact=True).check()
        page.get_by_label("شماره‌ی موبایل", exact=True).fill("123")
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(
            page.get_by_text("شماره‌ی موبایل باید ۱۱ رقم باشد و با ۰۹ شروع شود.", exact=True)
        ).to_be_visible()
        page.screenshot(path=str(tmp_path / f"sign-in-error-{width}.png"), full_page=True)
        page.get_by_label("شماره‌ی موبایل", exact=True).fill(PHONE)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page).to_have_url(live_server.url + reverse("login-code-verify"))
        expect(page.get_by_role("heading", name="کد ورود را وارد کنید", exact=True)).to_be_visible()
        expect(page.locator("#delivery-telegram")).to_be_visible()
        expect(page.locator("#delivery-sms")).to_be_hidden()
        expect(page.locator("#resend")).to_be_disabled()
        expect(page.locator("#sms-form button")).to_be_enabled()
        page.screenshot(path=str(tmp_path / f"telegram-code-{width}.png"), full_page=True)
        page.evaluate(PASTE_JS, "000000")
        expect(page.locator("#code-error")).to_be_visible()
        expect(page.locator(".otp__cell").first).to_have_value("")
        page.screenshot(path=str(tmp_path / f"telegram-code-error-{width}.png"), full_page=True)
        page.locator("#sms-form button").click()
        expect(page.locator("#delivery-sms")).to_be_visible()
        expect(page.locator("#sms-form")).to_be_hidden()
        assert len(sms.outbox) == 1 and len(queued) == 1
        page.screenshot(path=str(tmp_path / f"sms-code-{width}.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(PASTE_JS, code_from(sms.outbox))
        expect(page).to_have_url(live_server.url + reverse("account"))
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_checkout_keeps_its_long_cart_through_telegram_and_sms(
    browser, live_server, width, linked, queued, plan, tmp_path
):
    plan.title = (
        "یک‌ساله، طرح اختصاصی ابزار برنامه‌نویسی برای استفاده‌ی شخصی با پشتیبانی در تمام دوره"
    )
    plan.save(update_fields=["title"])
    client = Client()
    client.post(reverse("cart-add"), {"plan": plan.pk, "quantity": 2})
    context = browser.new_context(viewport={"width": width, "height": 900})
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
        expect(page.get_by_role("heading", name="ثبت سفارش", exact=True)).to_be_visible()
        expect(
            page.get_by_role("heading", name="برای ثبت سفارش وارد شوید", exact=True)
        ).to_be_visible()
        expect(page.get_by_label("پیامک", exact=True)).to_be_checked()
        page.screenshot(path=str(tmp_path / f"checkout-sign-in-{width}.png"), full_page=True)
        page.get_by_label("تلگرام", exact=True).check()
        page.get_by_label("شماره‌ی موبایل", exact=True).fill(PHONE)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page.locator("#delivery-telegram")).to_be_visible()
        expect(page.locator("[data-inline-resend] button")).to_be_disabled()
        expect(page.locator("#sms-form button")).to_be_enabled()
        page.screenshot(path=str(tmp_path / f"checkout-telegram-{width}.png"), full_page=True)
        page.locator("[name=code]").fill("000000")
        page.get_by_role("button", name="تأیید کد و ادامه", exact=True).click()
        expect(page.locator("[data-login-status]")).to_contain_text("درست نیست")
        page.screenshot(path=str(tmp_path / f"checkout-code-error-{width}.png"), full_page=True)
        page.locator("#sms-form button").click()
        expect(page.locator("#delivery-sms")).to_be_visible()
        expect(page.locator("#sms-form")).to_be_hidden()
        assert len(queued) == 1 and len(sms.outbox) == 1
        expect(page.locator("[name=code]")).to_have_value("")
        page.screenshot(path=str(tmp_path / f"checkout-sms-{width}.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("[name=code]").fill(code_from(sms.outbox))
        page.get_by_role("button", name="تأیید کد و ادامه", exact=True).click()
        expect(page.locator("[name=email]")).to_be_visible()
        expect(page).to_have_url(live_server.url + reverse("checkout"))
        expect(page.get_by_text(plan.title, exact=True)).to_be_visible()
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_empty_checkout_and_expired_telegram_code(browser, live_server, width, queued, tmp_path):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("checkout"))
        expect(page.get_by_role("heading", name="ثبت سفارش", exact=True)).to_be_visible()
        expect(page.get_by_text("سبد خرید شما خالی است", exact=True)).to_be_visible()
        page.screenshot(path=str(tmp_path / f"checkout-empty-{width}.png"), full_page=True)
        page.clock.install()
        page.goto(live_server.url + reverse("login-code"))
        page.get_by_label("تلگرام", exact=True).check()
        page.get_by_label("شماره‌ی موبایل", exact=True).fill(PHONE)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page).to_have_url(live_server.url + reverse("login-code-verify"))
        expect(page.get_by_role("heading", name="کد ورود را وارد کنید", exact=True)).to_be_visible()
        page.clock.fast_forward(601000)
        expect(page.locator("#code-clock")).to_have_text("کد منقضی شد")
        expect(page.locator(".otp__cell").first).to_be_disabled()
        expect(page.locator("#sms-form button")).to_be_enabled()
        page.screenshot(path=str(tmp_path / f"telegram-expired-{width}.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("channel", ["sms", "telegram"])
def test_code_sign_in_works_without_javascript(browser, live_server, channel, linked, queued, sent):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("login-code"))
        if channel == "telegram":
            page.get_by_label("تلگرام", exact=True).check()
        page.get_by_label("شماره‌ی موبایل", exact=True).fill(PHONE)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page).to_have_url(live_server.url + reverse("login-code-verify"))
        if channel == "telegram":
            deliver(queued[0])
        page.locator("[name=code]").fill(code_from(sent if channel == "telegram" else sms.outbox))
        page.get_by_role("button", name="ورود", exact=True).click()
        expect(page).to_have_url(live_server.url + reverse("account"))
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_changed_sign_in_views_have_no_serious_accessibility_violation(
    browser, live_server, width, linked, queued
):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("login-code"))
        expect(page.get_by_role("heading", name="ورود با کد", exact=True)).to_be_visible()
        assert not axe(page)
        page.get_by_label("تلگرام", exact=True).check()
        page.get_by_label("شماره‌ی موبایل", exact=True).fill(PHONE)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page).to_have_url(live_server.url + reverse("login-code-verify"))
        assert not axe(page)
        page.goto(live_server.url + reverse("checkout"))
        expect(page.get_by_role("heading", name="ثبت سفارش", exact=True)).to_be_visible()
        assert not axe(page)
    finally:
        context.close()


@pytest.mark.parametrize("accepted", [True, False])
def test_telegram_resend_keeps_sms_enabled_and_never_announces_delivery(
    browser, live_server, queued, accepted
):
    context = browser.new_context()
    page = context.new_page()
    try:
        page.clock.install()
        page.goto(live_server.url + reverse("login-code"))
        page.get_by_label("تلگرام", exact=True).check()
        page.get_by_label("شماره‌ی موبایل", exact=True).fill(PHONE)
        page.get_by_role("button", name="دریافت کد ورود", exact=True).click()
        expect(page).to_have_url(live_server.url + reverse("login-code-verify"))
        # The countdown starts when the page's script runs; jumping the clock before
        # that leaves the full wait ahead and the resend button disabled.
        expect(page.locator("#resend")).to_be_disabled()
        page.clock.fast_forward(121000)
        expect(page.locator("#resend")).to_be_enabled()
        page.route(
            "**/login/code/resend/",
            lambda route: route.fulfill(
                status=200 if accepted else 429,
                json=(
                    {"ok": True, "channel": "telegram", "expires_in": 600, "cooldown": 120}
                    if accepted
                    else {"ok": False, "error": "کد ورود فرستاده نشد."}
                ),
            ),
        )
        page.locator("#resend").click()
        if accepted:
            expect(page.locator("#code-status")).to_contain_text("اگر حساب تلگرامتان")
        else:
            expect(page.locator("#code-error")).to_be_visible()
        expect(page.locator("#sms-form button")).to_be_enabled()
    finally:
        context.close()

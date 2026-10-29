from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client

from apps.accounts.models import User
from apps.cms.models import Page

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("width", [390, 1440])
def test_drafts_and_long_persian_are_readable_without_javascript(browser, live_server, width):
    operator = User.objects.create_superuser("pages@example.test", "test-only")
    for slug, title in Page.Slug.choices:
        Page.objects.get_or_create(slug=slug, defaults={"title": title})
    refund = Page.objects.get(slug="refunds")
    refund.body = (
        "## پیش از فعال‌سازی\n\n"
        "اگر پیش از شروع فعال‌سازی یا تحویل محصول از خرید منصرف شوید، مبلغ پرداختی را کامل برمی‌گردانیم.\n\n"
        "## اشتراک ChatGPT Plus\n\n"
        + ("شرایط گارانتی هر محصول را پیش از خرید بخوانید. " * 25)
        + "\n\n[تماس با پشتیبانی](/contact/)\n\n"
        + "نشانی: https://example.test/"
        + "a" * 250
    )
    refund.save()
    client = Client()
    client.force_login(operator)
    context = browser.new_context(
        viewport={"width": width, "height": 900}, java_script_enabled=False
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
    try:
        page = context.new_page()
        for slug in Page.Slug.values:
            url = live_server.url + f"/{slug}/"
            response = page.goto(url)
            assert response.status == 200
            assert page.url == url
            assert page.locator("h1").inner_text() == str(Page.Slug(slug).label)
            assert page.get_by_role("status").filter(has_text="پیش‌نویس").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert (
                page.locator(".legal-page").evaluate("el => getComputedStyle(el).direction")
                == "rtl"
            )
            if slug == "refunds":
                assert (
                    page.locator(".legal-page__body bdi")
                    .filter(has_text="ChatGPT Plus")
                    .is_visible()
                )
            elif slug == "contact":
                assert page.locator(".legal-page__support").is_visible()
            else:
                assert page.get_by_text("متن این صفحه هنوز نوشته نشده است.").is_visible()
        page.goto(live_server.url + "/refunds/")
        page.keyboard.press("Tab")
        assert page.locator(":focus").count() == 1
        assert page.locator(":focus").evaluate("el => getComputedStyle(el).outlineStyle") != "none"
        page.get_by_role("link", name="تماس با پشتیبانی", exact=True).click()
        assert page.url == live_server.url + "/contact/"
        context.clear_cookies()
        assert page.goto(live_server.url + "/refunds/?preview=1").status == 404
    finally:
        context.close()


def test_refund_preview_has_no_serious_accessibility_violations(browser, live_server):
    operator = User.objects.create_superuser("access@example.test", "test-only")
    Page.objects.get_or_create(
        slug="refunds",
        defaults={
            "title": "لغو سفارش و بازگشت وجه",
            "body": "## انصراف از خرید\n\nشماره‌ی سفارش را بفرستید.",
        },
    )
    client = Client()
    client.force_login(operator)
    context = browser.new_context(viewport={"width": 390, "height": 900})
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    try:
        page = context.new_page()
        assert page.goto(live_server.url + "/refunds/").status == 200
        page.add_script_tag(path=str(Path(settings.BASE_DIR) / "node_modules/axe-core/axe.min.js"))
        result = page.evaluate("async () => await axe.run(document)")
        assert result["passes"]
        assert not [v for v in result["violations"] if v["impact"] in {"serious", "critical"}]
    finally:
        context.close()

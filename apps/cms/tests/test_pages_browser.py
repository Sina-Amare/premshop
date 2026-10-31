from importlib import import_module
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client

from apps.accounts.models import User
from apps.cms.models import Page

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("javascript", [False, True])
def test_complete_legal_drafts_and_descriptions_render_privately(
    browser, live_server, width, javascript
):
    migration = import_module("apps.cms.migrations.0004_legal_page_drafts")
    for slug, draft in migration.DRAFTS.items():
        Page.objects.update_or_create(slug=slug, defaults={**draft, "is_published": False})
    operator = User.objects.create_superuser("copy-review@example.test", "test-only")
    client = Client()
    client.force_login(operator)
    context = browser.new_context(
        viewport={"width": width, "height": 900}, java_script_enabled=javascript
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
        for slug, draft in migration.DRAFTS.items():
            url = live_server.url + f"/{slug}/"
            response = page.goto(url)
            assert response.status == 200
            assert page.url == url
            assert page.locator("h1").inner_text() == draft["title"]
            assert (
                page.locator("meta[name='description']").get_attribute("content")
                == draft["description"]
            )
            assert response.headers["x-robots-tag"] == "noindex, nofollow"
            assert {"private", "no-store"} <= set(response.headers["cache-control"].split(", "))
            if slug in {"terms", "refunds"}:
                landmark = "مهلت تحویل" if slug == "terms" else "انصراف پس از پرداخت"
                assert page.get_by_role("heading", name=landmark, exact=True).is_visible()
            else:
                assert page.locator(".legal-page__body h2, .legal-page__body h3").count() == 0
            assert "Vazirmatn" in page.locator("body").evaluate(
                "el => getComputedStyle(el).fontFamily"
            )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if slug == "privacy":
                assert (
                    page.locator(".legal-page__body bdi").filter(has_text="IP").first.is_visible()
                )
                assert (
                    page.get_by_role("link", name="به پشتیبانی پیام بدهید").get_attribute("href")
                    == "/contact/"
                )
            elif slug == "contact":
                assert page.locator(".legal-page__support").is_visible()
        context.clear_cookies()
        for slug in migration.DRAFTS:
            assert page.goto(live_server.url + f"/{slug}/?preview=1").status == 404
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_published_footer_links_wrap_as_a_row(browser, live_server, width):
    for slug, title in Page.Slug.choices:
        Page.objects.update_or_create(
            slug=slug,
            defaults={
                "title": title,
                "body": "متن نهایی صفحه.",
                "description": "توضیح صفحه.",
                "is_published": True,
            },
        )
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        assert page.goto(live_server.url + "/refunds/").status == 200
        links = page.locator(".v-footer__legal")
        assert links.locator("a").count() == 5
        assert links.evaluate("el => getComputedStyle(el).flexDirection") == "row"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_drafts_and_long_persian_are_readable_without_javascript(browser, live_server, width):
    operator = User.objects.create_superuser("pages@example.test", "test-only")
    for slug, title in Page.Slug.choices:
        Page.objects.update_or_create(
            slug=slug,
            defaults={"title": title, "body": "", "description": "", "is_published": False},
        )
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

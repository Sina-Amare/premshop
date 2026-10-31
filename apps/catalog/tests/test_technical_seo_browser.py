"""Unavailable product pages keep their content and cannot offer a purchase."""

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.catalog.models import Product
from apps.catalog.tests.test_technical_seo import product as product

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("javascript", [True, False])
def test_unavailable_page_has_content_without_purchase_controls(
    browser, live_server, product, width, javascript, tmp_path
):
    context = browser.new_context(
        viewport={"width": width, "height": 900}, java_script_enabled=javascript
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        response = page.goto(live_server.url + product.get_absolute_url())
        assert response.status == 200
        expect(page.locator("h1")).to_have_text(product.name)
        expect(page.locator(".pdp__lead")).to_have_text(product.short_description)
        expect(page.locator(".buybox__cta")).to_be_disabled()
        expect(page.locator(".buybox__cta")).to_have_text("ناموجود")
        expect(page.locator("#buy-form, [data-buy-bar], .preview-bar")).to_have_count(0)
        assert page.locator('meta[name="robots"][content="noindex"]').count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.url == live_server.url + product.get_absolute_url()
        page.screenshot(
            path=str(tmp_path / f"unavailable-{width}-{javascript}.png"), full_page=True
        )
        page.locator("footer").scroll_into_view_if_needed()
        expect(page.locator("[data-buy-bar]")).to_have_count(0)
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("state", ["active", "draft", "unavailable", "incomplete"])
def test_panel_shows_slug_editability_and_unavailable_page_reasons(
    browser, live_server, admin_client, settings, product, width, state, tmp_path
):
    product.status = Product.Status.UNAVAILABLE if state == "incomplete" else state
    if state == "incomplete":
        product.usage_notes = ""
        product.slug = "adobe-cc"
        product.name = "Adobe Creative Cloud"
    product.save()
    context = browser.new_context(viewport={"width": width, "height": 1100})
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": admin_client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    try:
        path = reverse("admin:catalog_product_change", args=[product.pk])
        response = page.goto(live_server.url + path)
        assert response.status == 200
        assert page.url == live_server.url + path
        expect(page.locator("#id_name")).to_have_value(product.name)
        if state == "draft":
            expect(page.locator('input[name="slug"]')).to_have_value(product.slug)
        else:
            expect(page.locator('input[name="slug"]')).to_have_count(0)
            expect(page.get_by_text(product.slug, exact=True)).to_be_visible()
        expect(page.locator(".field-status")).to_contain_text("قابل خرید نیست")
        if state == "incomplete":
            expect(page.locator(".panel-readiness")).to_contain_text(
                "صفحه‌ی این محصول نمایش داده نمی‌شود"
            )
            for reason in product.publish_problems():
                expect(page.locator(".panel-readiness")).to_contain_text(reason)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / f"panel-{state}-{width}.png"))
    finally:
        context.close()

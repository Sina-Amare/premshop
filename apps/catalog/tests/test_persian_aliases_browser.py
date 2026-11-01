"""Persian search results and the shared publishing warning at both widths."""

from urllib.parse import urlencode

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.catalog.models import Product
from apps.catalog.tests.test_persian_aliases import catalog as catalog

pytestmark = [pytest.mark.django_db, pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("query,count", [("گیفت کارت", 6), ("پرپلکسیتی", 1), ("وی پی ان", 0)])
def test_search_results_show_category_alias_and_empty_states(
    browser, live_server, catalog, width, query, count, tmp_path
):
    context = browser.new_context(viewport={"width": width, "height": 1000})
    page = context.new_page()
    try:
        url = live_server.url + reverse("search") + "?" + urlencode({"q": query})
        response = page.goto(url)
        assert response.status == 200
        assert page.url == url
        expect(page.locator("h1")).to_contain_text("نتایج جستجو" if count else "محصولی پیدا نکردیم")
        expect(page.locator(".v-query")).to_have_text(query)
        if count:
            expect(page.locator(".grid .pcard")).to_have_count(count)
        else:
            expect(page.get_by_role("heading", name="همه‌ی محصولات")).to_have_count(1)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / f"search-{width}-{count}.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_panel_names_the_missing_persian_alias(
    browser, live_server, catalog, admin_client, settings, width, tmp_path
):
    product = catalog.get(slug="perplexity-pro")
    product.status = Product.Status.UNAVAILABLE
    product.aliases = "perplexity"
    product.save()
    context = browser.new_context(viewport={"width": width, "height": 1000})
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
        url = live_server.url + reverse("admin:catalog_product_change", args=[product.pk])
        response = page.goto(url)
        assert response.status == 200
        assert page.url == url
        expect(page.locator("#id_name")).to_have_value(product.name)
        warning = page.get_by_text(product.fact_problems()["aliases"], exact=True)
        expect(warning).to_be_visible()
        warning.scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / f"alias-warning-{width}.png"), full_page=True)
    finally:
        context.close()

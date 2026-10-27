import re
from pathlib import Path

import pytest
from django.conf import settings
from playwright.sync_api import expect

from apps.catalog.tests.test_admin_browser import change_form
from apps.catalog.tests.test_admin_browser import open_panel as open_panel
from apps.catalog.tests.test_admin_browser import product as product
from apps.core.tests.test_accessibility_browser import contrast

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def test_composite_promotion_fields_have_one_context_note(open_panel, product):
    plan = product.plans.first()
    page = open_panel(f"/admin/catalog/plan/{plan.pk}/change/", width=390)
    for name in ["promo_starts_at", "promo_ends_at"]:
        assert (
            page.locator(f"#id_{name}_0").evaluate(
                "e=>e.closest('.field-line').querySelectorAll('.field-placement').length"
            )
            == 1
        )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_brand_validation_message_meets_AA_in_both_themes(open_panel, product, theme):
    page = open_panel(change_form(product))
    page.evaluate("theme=>localStorage.setItem('adminTheme',JSON.stringify(theme))", theme)
    page.reload()
    page.locator("#id_brand_colour").fill("invalid")
    error = page.locator("[data-brand-error]")
    expect(error).to_be_visible()
    foreground = error.evaluate("e=>getComputedStyle(e).color")
    background = error.evaluate("e=>getComputedStyle(e.closest('fieldset')).backgroundColor")
    assert contrast(foreground, background) >= 4.5


@pytest.mark.parametrize("saved_image", [False, True])
def test_clearing_an_uploaded_image_restores_saved_art_or_monogram(
    open_panel, product, saved_image
):
    if saved_image:
        product.image = "products/preview-original.png"
        product.save()
    page = open_panel(change_form(product))
    pixels = (Path(settings.BASE_DIR) / "static/img/favicon-32.png").read_bytes()
    page.route(
        "**/media/products/preview-original.png",
        lambda route: route.fulfill(content_type="image/png", body=pixels),
    )
    if saved_image:
        page.reload()
    art = page.locator("[data-brand-preview] .pcard__art")
    original = art.locator("img").get_attribute("src") if saved_image else None
    image = page.locator("#id_image")
    image.set_input_files({"name": "new.png", "mimeType": "image/png", "buffer": pixels})
    expect(art.locator("img")).to_have_attribute("src", re.compile("^blob:"))
    image.set_input_files([])
    if saved_image:
        expect(art.locator("img")).to_have_attribute("src", original)
        page.locator('[name="image-clear"]').check()
        expect(art.locator("img")).to_have_count(0)
        expect(art.locator(".pcard__mono")).to_be_visible()
        page.locator('[name="image-clear"]').uncheck()
        expect(art.locator("img")).to_have_attribute("src", original)
    else:
        expect(art.locator("img")).to_have_count(0)
        expect(art.locator(".pcard__mono")).to_be_visible()

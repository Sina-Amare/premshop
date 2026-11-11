"""The five status tones as a browser draws them. A colour defined in an `@layer` of
input.css loses silently to anything unlayered in storefront.css, so the computed
colour is the proof, not the stylesheet."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.browser]

# The words' colour each tone must reach on a customer page, storefront remapping included.
WORDS = {
    "time": "rgb(146, 64, 14)",
    "progress": "rgb(21, 94, 117)",
    "ok": "rgb(22, 101, 52)",
    "neutral": "rgb(86, 97, 112)",
    "danger": "rgb(153, 27, 27)",
}


@pytest.mark.parametrize("width", [390, 1440])
def test_each_tone_draws_its_own_colour_and_icon(browser, live_server, settings, width, tmp_path):
    settings.DEBUG = True  # the style guide is shown to staff, or to anyone while debugging
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(live_server.url + reverse("styleguide"))
        assert page.locator("#status-tones .badge").count() == len(WORDS)
        for tone, colour in WORDS.items():
            pill = page.locator(f"#status-tones .badge--{tone}")
            assert pill.evaluate("el => getComputedStyle(el).color") == colour, tone
            icon = pill.locator("svg").bounding_box()
            assert icon and round(icon["width"]) == 14, tone
            assert pill.evaluate("el => el.closest('a, button') === null"), tone
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.locator("#status-tones").screenshot(path=str(tmp_path / f"tones-{width}.png"))
    finally:
        page.close()

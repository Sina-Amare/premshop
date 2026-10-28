"""Offers move only when the visitor asks."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def test_offers_stay_still_after_focus_leaves_and_advance_manually(browser, live_server):
    category = Category.objects.create(name="محصولات", slug="services")
    for i in range(8):
        product = Product.objects.create(
            category=category, name=f"Service {i}", slug=f"service-{i}", status="active"
        )
        Plan.objects.create(
            product=product,
            title="۱ ماهه",
            cost_price=100,
            sale_price=200,
            promo_price=150,
            promo_ends_at=timezone.now() + timedelta(days=2),
        )
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.clock.install()
        page.goto(live_server.url + "/")
        rail = page.locator("[data-rail]").first
        rail.scroll_into_view_if_needed()
        rail.locator("[data-rail-next]").focus()
        page.mouse.move(0, 0)
        track = rail.locator("[data-rail-track]")
        before = track.evaluate("e=>e.scrollLeft")
        page.clock.fast_forward(13000)
        assert track.evaluate("e=>e.scrollLeft") == before
        page.evaluate("document.activeElement.blur()")
        page.clock.fast_forward(13000)
        assert track.evaluate("e=>e.scrollLeft") == before
        rail.locator("[data-rail-next]").click()
        page.clock.run_for(500)
        assert abs(track.evaluate("e=>e.scrollLeft")) > 50
        assert (
            rail.locator(".v-section-mark").evaluate("e=>getComputedStyle(e).animationName")
            == "none"
        )
    finally:
        page.close()

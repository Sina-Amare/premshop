from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from apps.catalog.models import Plan


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_polling_refreshes_changed_offer_deadlines_and_removed_promotions(
    browser, live_server, plan
):
    Plan.objects.filter(pk=plan.pk).update(
        promo_price=150000, promo_ends_at=timezone.now() + timedelta(hours=1)
    )
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        Plan.objects.filter(pk=plan.pk).update(promo_ends_at=timezone.now() + timedelta(hours=3))
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function("+document.querySelector('.plans .deadline').dataset.left > 10000")
        Plan.objects.filter(pk=plan.pk).update(promo_price=None)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator(".plan__promotion")).to_have_count(0)
        expect(page.locator(".plans .price__num")).to_have_text("۲۰۰,۰۰۰")
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_polling_keeps_an_updated_regular_price_behind_a_live_discount(browser, live_server, plan):
    Plan.objects.filter(pk=plan.pk).update(
        promo_price=150000, promo_ends_at=timezone.now() + timedelta(hours=1)
    )
    page = browser.new_page()
    try:
        page.clock.install()
        page.goto(live_server.url + plan.product.get_absolute_url())
        expect(page.locator(".plans .price__was")).to_contain_text("۲۰۰,۰۰۰")
        Plan.objects.filter(pk=plan.pk).update(sale_price=300000)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator(".plans .price__was")).to_contain_text("۳۰۰,۰۰۰")
        page.clock.fast_forward("02:00:00")
        expect(page.locator(".plans .price__num")).to_have_text("۳۰۰,۰۰۰")
    finally:
        page.close()

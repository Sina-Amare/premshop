from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from apps.catalog.models import Plan

pytestmark = pytest.mark.django_db


def _cart_page(body):
    return body.split('class="wrap cart-page"', 1)[1].split("</main>", 1)[0]


def _line(body, plan):
    return body.split(f'data-cart-plan="{plan.pk}"', 1)[1].split("</article>", 1)[0]


def _saving_row(body):
    return body.split('<p class="cart-summary__saving">', 1)[1].split("</p>", 1)[0]


def _promote(plan, **extra):
    Plan.objects.filter(pk=plan.pk).update(promo_price=150000, **extra)


def _open_cart(browser, live_server, client, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page.goto(live_server.url + "/cart/")
    return page


def test_promoted_line_strikes_its_list_price_and_the_summary_states_the_saving(client, plan):
    regular = Plan.objects.create(
        product=plan.product, title="سه‌ماهه", cost_price=1, sale_price=500000
    )
    _promote(plan, promo_ends_at=timezone.now() + timedelta(hours=2))
    client.post("/cart/add/", {"plan": plan.pk, "quantity": 2})
    client.post("/cart/add/", {"plan": regular.pk})
    response = client.get("/cart/")
    body = response.content.decode()
    assert response.context["cart"].saving == Decimal(100000)
    promoted = _line(body, plan)
    assert (
        '<del class="price__was"><span class="sr-only">قیمت قبلی </span>۲۰۰,۰۰۰</del>' in promoted
    )
    assert '<span class="price__num">۱۵۰,۰۰۰</span>' in promoted
    assert 'class="badge badge--time deadline"' in promoted
    assert "price__was" not in _line(body, regular)
    summary = body.split('<aside class="cart-summary"', 1)[1]
    assert summary.index("cart-summary__saving") < summary.index("cart-summary__total")
    assert _saving_row(body).startswith("<span>تخفیف</span>")
    assert '<span class="price__num">۱۰۰,۰۰۰</span>' in _saving_row(body)


def test_a_single_promoted_unit_still_shows_both_prices(client, plan):
    _promote(plan)
    client.post("/cart/add/", {"plan": plan.pk})
    promoted = _line(client.get("/cart/").content.decode(), plan)
    assert "۲۰۰,۰۰۰</del>" in promoted
    assert "deadline" not in promoted


def test_cart_without_a_promotion_shows_no_struck_price_or_saving_row(client, plan):
    client.post("/cart/add/", {"plan": plan.pk, "quantity": 2})
    response = client.get("/cart/")
    page = _cart_page(response.content.decode())
    assert response.context["cart"].saving == 0
    assert "price__was" not in page
    assert "cart-summary__saving" not in page
    assert "تخفیف" not in page


def test_saving_counts_only_the_lines_the_total_counts(client, plan):
    other = Plan.objects.create(
        product=plan.product, title="سه‌ماهه", cost_price=1, sale_price=500000, promo_price=400000
    )
    _promote(plan)
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/add/", {"plan": other.pk})
    Plan.objects.filter(pk=other.pk).update(is_available=False)
    cart = client.get("/cart/").context["cart"]
    assert cart.total == 150000
    assert cart.saving == 50000


def test_scripted_quantity_change_and_removal_rerender_the_saving(client, plan):
    regular = Plan.objects.create(
        product=plan.product, title="سه‌ماهه", cost_price=1, sale_price=500000
    )
    _promote(plan)
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/add/", {"plan": regular.pk})
    assert "۵۰,۰۰۰" in _saving_row(client.get("/cart/").content.decode())
    # cart.js posts the change as JSON, then rebuilds the cart from the page itself.
    stepped = client.post(
        "/cart/quantity/",
        {"plan": plan.pk, "quantity": 1, "step": 1},
        HTTP_ACCEPT="application/json",
    )
    assert stepped.status_code == 200
    assert "۱۰۰,۰۰۰" in _saving_row(client.get("/cart/").content.decode())
    removed = client.post("/cart/remove/", {"plan": plan.pk}, HTTP_ACCEPT="application/json")
    assert removed.status_code == 200
    page = _cart_page(client.get("/cart/").content.decode())
    assert "cart-summary__saving" not in page
    assert "price__was" not in page


def test_cart_line_carries_the_polled_price_key(client, plan):
    _promote(plan, promo_ends_at=timezone.now() + timedelta(hours=2))
    client.post("/cart/add/", {"plan": plan.pk})
    body = client.get("/cart/").content.decode()
    options = client.get("/cart/availability/", {"plan": plan.pk}).json()["options"]
    assert f'data-cart-price="{options[str(plan.pk)]["price_key"]}"' in body


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [390, 1440])
def test_stepping_a_promoted_line_updates_the_saving_row(browser, live_server, client, plan, width):
    _promote(plan, promo_ends_at=timezone.now() + timedelta(hours=2))
    client.post("/cart/add/", {"plan": plan.pk})
    page = _open_cart(browser, live_server, client, width)
    try:
        saving = page.locator(".cart-summary__saving")
        expect(saving).to_contain_text("۵۰,۰۰۰")
        page.locator('[name=step][value="1"]').click()
        expect(page.locator(".cart-line__amount")).to_contain_text("۳۰۰,۰۰۰")
        expect(saving).to_contain_text("۱۰۰,۰۰۰")
        expect(page.locator(".cart-line .price__was")).to_have_text("قیمت قبلی ۲۰۰,۰۰۰")
        # The rebuilt countdown must tick, or it would freeze at the time of the edit.
        expect(page.locator(".cart-line .deadline[data-clock-ready]")).to_have_count(1)
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        directory = Path("screenshots/cart-discount")
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=directory / f"{width}.png", full_page=True)
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_poll_rereads_a_list_price_changed_behind_a_live_discount(
    browser, live_server, client, plan
):
    _promote(plan)
    client.post("/cart/add/", {"plan": plan.pk})
    page = _open_cart(browser, live_server, client, 1440)
    try:
        expect(page.locator(".cart-summary__saving")).to_contain_text("۵۰,۰۰۰")
        Plan.objects.filter(pk=plan.pk).update(sale_price=300000)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator(".cart-line .price__was")).to_have_text("قیمت قبلی ۳۰۰,۰۰۰")
        expect(page.locator(".cart-summary__saving")).to_contain_text("۱۵۰,۰۰۰")
    finally:
        page.close()

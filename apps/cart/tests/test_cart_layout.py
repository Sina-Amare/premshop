from decimal import Decimal

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.cart.models import Cart, CartItem
from apps.cart.services import cart_summary
from apps.catalog.models import Plan
from apps.catalog.promises import PROMISES
from apps.core.support import details

pytestmark = pytest.mark.django_db


def test_subtotal_counts_the_same_purchasable_lines_as_total(plan, buyer):
    cart = Cart.objects.create(user=buyer)
    plan.promo_price = Decimal(150000)
    plan.save()
    CartItem.objects.create(cart=cart, plan=plan, quantity=2)
    for title, fields, quantity in [
        ("Regular", {}, 1),
        ("Unavailable", {"is_available": False}, 1),
        ("Invalid", {"max_quantity": 1}, 2),
    ]:
        option = Plan.objects.create(
            product=plan.product, title=title, cost_price=1, sale_price=300000, **fields
        )
        CartItem.objects.create(cart=cart, plan=option, quantity=quantity)
    summary = cart_summary(cart)
    assert summary.subtotal == 700000
    assert summary.saving == 100000
    assert summary.subtotal - summary.saving == summary.total == 600000


@pytest.mark.parametrize("discount", [True, False])
def test_summary_shows_its_arithmetic_and_confirmed_help(client, plan, discount):
    if discount:
        plan.promo_price = Decimal(150000)
        plan.save()
    client.post("/cart/add/", {"plan": plan.pk})
    body = client.get("/cart/").content.decode()
    if discount:
        assert (
            body.index("قیمت محصولات")
            < body.index("cart-summary__saving")
            < body.index("cart-summary__total")
        )
        assert "۲۰۰,۰۰۰" in body and "۵۰,۰۰۰" in body and "۱۵۰,۰۰۰" in body
    else:
        assert "قیمت محصولات" not in body and "cart-summary__saving" not in body
    assert PROMISES[0].text in body
    assert details()["hours"] in body and details()["telegram_url"] in body
    assert "افزودن محصول دیگر" not in body
    assert '<button class="btn btn--primary" type="button" disabled>ثبت سفارش</button>' in body
    assert "ثبت سفارش هنوز فعال نیست." in body
    assert "cart-line__amount-label" not in body and "cart-fixed-quantity" not in body
    client.post("/cart/quantity/", {"plan": plan.pk, "quantity": 2})
    assert "جمع ۲ عدد" in client.get("/cart/").content.decode()


def test_empty_cart_explains_sign_in_only_to_visitors(client, buyer):
    body = client.get("/cart/").content.decode()
    assert body.index("برای شروع، یک محصول انتخاب کنید.") < body.index("مشاهده‌ی محصولات")
    assert f'href="{reverse("login")}?next=/cart/"' in body
    client.force_login(buyer)
    assert "cart-empty__signin" not in client.get("/cart/").content.decode()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [390, 1440])
def test_cart_controls_and_total_fit_the_new_layout(browser, live_server, plan, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        page.goto(live_server.url + "/cart/")
        expect(page.locator("h1")).to_contain_text("سبد خرید")
        expect(page.locator(".cart-back")).to_have_text("بازگشت به محصولات")
        assert page.locator(".cart-back").get_attribute("href") == "/#products"
        assert (
            page.locator(".cart-summary__total .price__num").evaluate(
                "e => getComputedStyle(e).fontVariantNumeric"
            )
            == "proportional-nums"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for control in page.locator(".cart-remove,.cart-stepper button").all():
            box = control.bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44
            assert control.get_attribute("aria-label")
        if width == 390:
            step = page.locator(".cart-stepper").bounding_box()
            remove = page.locator(".cart-remove").bounding_box()
            assert abs(step["y"] - remove["y"]) < 4
        page.locator(".cart-remove").click()
        expect(page.locator(".cart-empty")).to_be_visible()
        assert page.locator(".cart-empty .btn").get_attribute("href") == "/#products"
    finally:
        page.close()

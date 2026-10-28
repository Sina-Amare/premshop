from pathlib import Path

import pytest
from playwright.sync_api import expect

from apps.cart.models import CartItem
from apps.cart.tests.test_toast_plan_total import LOGO
from apps.catalog.tests.test_admin_browser import open_panel as open_panel

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_untimed_cart_keeps_unavailable_selections_until_explicit_removal(
    browser, live_server, plan, width
):
    plan.stock_quantity = 2
    plan.save()
    # A logo narrows the toast's text column, as every seeded product's does.
    plan.product.image = "products/sample.svg"
    plan.product.save()
    directory = Path("screenshots/cart-stock")
    directory.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(viewport={"width": width, "height": 1000})
    other_context = browser.new_context(viewport={"width": width, "height": 1000})
    for each in (context, other_context):
        each.route(
            f"**{plan.product.image.url}",
            lambda route: route.fulfill(content_type="image/svg+xml", body=LOGO),
        )
    page, other = context.new_page(), other_context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator("#qty").fill("2")
        expect(page.get_by_role("button", name="بیشتر", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="بیشتر", exact=True)).to_be_disabled()
        expect(page.locator("#qty-limit")).to_contain_text("۲ عدد")
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        expect(page.locator("#cart-toast .cart-toast__art")).to_be_visible()
        toast = page.locator("#cart-toast").bounding_box()
        header = page.locator(".v-header").bounding_box()
        purchase = page.locator(".buybox__cart").bounding_box()
        cart = page.locator(".v-cart").bounding_box()
        # Phones: the width of the screen; wider: hung under the cart icon it reports on.
        anchor = width / 2 if width < 720 else cart["x"] + cart["width"] / 2
        assert abs(toast["x"] + toast["width"] / 2 - anchor) <= 1
        assert toast["y"] >= header["y"] + header["height"] + 8
        assert toast["height"] <= 140 and toast["width"] <= 400
        assert toast["y"] + toast["height"] <= purchase["y"]
        expect(page).to_have_url(live_server.url + plan.product.get_absolute_url())
        expect(page.locator(".v-cart__count")).to_have_text("۲")
        expect(page.locator(".buybox__cart")).to_be_visible()
        assert page.locator("#cart-toast").count() == 1
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(directory / f"toast-{width}.png"), full_page=True)
        other.goto(live_server.url + plan.product.get_absolute_url())
        expect(other.locator(".buybox__cta")).to_be_enabled()
        page.locator("#cart-toast a").click()
        expect(page.locator("[data-reservation-clock]")).to_have_count(0)
        expect(page.locator('.cart-stepper [value="1"]')).to_be_visible()
        expect(page.locator('.cart-stepper [value="1"]')).to_be_disabled()
        page.screenshot(path=str(directory / f"selected-{width}.png"), full_page=True)
        page.locator('.cart-stepper [value="-1"]').click()
        expect(page.locator(".cart-quantity input[name=quantity]")).to_have_value("1")
        plan.stock_quantity = 0
        plan.save()
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator(".cart-line__stock")).to_have_text("ناموجود")
        expect(page.locator(".cart-line__warning")).to_have_text(
            "برای ادامه‌ی خرید، این مورد را حذف کنید."
        )
        expect(page.locator(".cart-line__restock")).to_be_visible()
        assert CartItem.objects.get().quantity == 1
        expect(page.locator(".cart-line__amount")).to_have_count(0)
        expect(page.locator(".cart-summary__total")).not_to_contain_text("رایگان")
        page.screenshot(path=str(directory / f"unavailable-{width}.png"), full_page=True)
        page.locator(".cart-remove").click()
        expect(page.locator(".cart-empty")).to_be_visible()
        plan.stock_quantity = 2
        plan.save()
        other.reload()
        expect(other.locator("#qty")).to_have_attribute("max", "2")
        assert not errors
    finally:
        context.close()
        other_context.close()


def test_rapid_clicks_submit_once_and_toasts_never_stack(browser, live_server, plan):
    page = browser.new_page(viewport={"width": 390, "height": 1000})
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").evaluate("button => { for(let i=0;i<30;i++) button.click(); }")
        expect(page.locator("#cart-toast")).to_be_visible()
        assert CartItem.objects.get().quantity == 1
        expect(page).to_have_url(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator(".v-cart__count")).to_have_text("۲")
        assert page.locator(".cart-toast:visible").count() == 1
        page.locator("[data-toast-close]").click()
        expect(page.locator("#cart-toast")).to_be_hidden()
        expect(page.locator(".v-cart")).to_be_focused()
    finally:
        page.close()


@pytest.mark.parametrize("initial_stock", [0, 1])
def test_increased_stock_updates_quantity_and_price_on_an_open_product_page(
    browser, live_server, plan, initial_stock
):
    plan.stock_quantity = initial_stock
    plan.save()
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        expect(page.locator("#qty-row")).to_be_hidden()
        plan.stock_quantity = 2
        plan.save()
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator("#qty-row")).to_be_visible()
        expect(page.locator("#qty")).to_have_attribute("max", "2")
        expect(page.locator(".buybox__cta")).to_be_enabled()
        expect(page.locator("[data-plan-price] .price__num").first).to_be_visible()
    finally:
        page.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_admin_stock_section_and_live_counts(open_panel, plan, width):
    from apps.cart.services import add_to_cart
    from apps.cart.tests.test_services import request_for

    plan.stock_quantity = 3
    plan.save()
    add_to_cart(request_for(), plan_id=plan.pk, quantity=2)
    page = open_panel(f"/admin/catalog/product/{plan.product_id}/change/", width=width)
    stock = page.locator("#id_plans-0-stock_quantity")
    stock.scroll_into_view_if_needed()
    expect(stock).to_have_value("3")
    expect(page.get_by_text("۳ عدد قابل خرید", exact=True)).to_be_visible()
    expect(page.get_by_text("موجودی و تعداد", exact=True).first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    directory = Path("screenshots/cart-stock")
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / f"admin-stock-{width}.png"), full_page=True)

from pathlib import Path

import pytest
from playwright.sync_api import expect

from apps.catalog.models import Plan, Product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_cart_empty_populated_error_unavailable_and_keyboard(browser, live_server, plan, width):
    plan.product.name = "اشتراک سرویس نمونه برای حساب شخصی با عنوان بلند Example Pro Family"
    plan.product.save()
    other_product = Product.objects.create(
        category=plan.product.category,
        name="محصول دوم",
        slug="second",
        status=Product.Status.ACTIVE,
    )
    other = Plan.objects.create(
        product=other_product, title="پلن دوم", sale_price=150000, cost_price=100000
    )
    directory = Path("screenshots/persistent-cart")
    directory.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(viewport={"width": width, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def inspect(state):
        assert page.url == live_server.url + "/cart/"
        expect(page.locator("h1")).to_contain_text("سبد خرید")
        if state != "empty":
            expect(page.locator(".cart-heading__count")).to_have_text("۲ محصول")
        page.evaluate("document.fonts.ready")
        assert page.evaluate(
            "[...document.fonts].some(f => f.family.includes('Estedad') && f.status === 'loaded')"
        )
        expect(page.locator(".v-cart svg")).to_be_visible()
        expect(page.locator(".v-cart")).to_have_accessible_name(
            "سبد خرید، " + ("۰" if state == "empty" else "۳") + " عدد"
        )
        for selector, minimum in ((".cart-line h2", 16), (".cart-line__plan", 14)):
            for element in page.locator(selector).all():
                assert (
                    element.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= minimum
                )
        for control in page.locator(
            ".cart-remove:visible, .cart-apply:visible, .cart-stepper button:visible"
        ).all():
            box = control.bounding_box()
            assert box and box["width"] >= 44 and box["height"] >= 44
            assert control.get_attribute("aria-label")
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert page.locator('input[type="password"]').count() == 0
        for amount in page.locator(".cart-money").all():
            box = amount.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1
            assert amount.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        page.screenshot(path=str(directory / f"{state}-{width}.png"), full_page=True)

    try:
        page.goto(live_server.url + "/cart/")
        inspect("empty")
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#cart-toast")).to_be_visible()
        expect(page).to_have_url(live_server.url + plan.product.get_absolute_url())
        page.locator("#cart-toast a").click()
        expect(page).to_have_url(live_server.url + "/cart/")
        expect(page.locator(".cart-line")).to_have_count(1)
        token = page.locator('[name="csrfmiddlewaretoken"]').first.input_value()
        response = page.request.post(
            live_server.url + "/cart/add/",
            form={
                "plan": str(other.pk),
                "quantity": "1",
                "csrfmiddlewaretoken": token,
            },
        )
        assert response.ok
        page.reload()
        expect(page.locator(".cart-line")).to_have_count(2)
        page.locator(f"#quantity-{plan.pk}").fill("2")
        page.locator(".cart-quantity").filter(has=page.locator(f"#quantity-{plan.pk}")).locator(
            ".cart-apply"
        ).click()
        expect(page.locator(".v-cart__count")).to_have_text("۳")
        inspect("populated")
        page.locator(f'[data-cart-plan="{plan.pk}"] [name=step][value="1"]').click()
        expect(page.locator(f"#quantity-{plan.pk}")).to_have_value("3")
        page.locator(f'[data-cart-plan="{plan.pk}"] [name=step][value="-1"]').click()
        expect(page.locator(f"#quantity-{plan.pk}")).to_have_value("2")
        page.locator(f"#quantity-{plan.pk}").evaluate(
            "el => { el.max = '10'; el.value = '4'; el.form.requestSubmit(); }"
        )
        expect(page.get_by_role("alert")).to_contain_text("حداکثر تعداد")
        inspect("error")
        assert page.locator(f"#quantity-{plan.pk}").input_value() == "4"
        Plan.objects.filter(pk=plan.pk).update(sale_price=999999999990)
        page.goto(live_server.url + "/cart/")
        inspect("large-price")
        Plan.objects.filter(pk=plan.pk).update(sale_price=200000)
        Product.objects.filter(pk=plan.product_id).update(status=Product.Status.DRAFT)
        page.goto(live_server.url + "/cart/")
        expect(page.locator(".cart-line").first.locator(".cart-line__stock")).to_have_text("ناموجود")
        assert plan.product.name not in page.locator("main").inner_text()
        inspect("unavailable")
        page.get_by_role("button", name="حذف محصول ناموجود", exact=True).click()
        expect(page.locator(".cart-line")).to_have_count(1)
        assert not errors
    finally:
        context.close()


def test_real_browser_profile_keeps_guest_cart_after_restart(browser, live_server, plan, tmp_path):
    profile = str(tmp_path / "profile")
    context = browser.browser_type.launch_persistent_context(profile)
    try:
        page = context.pages[0]
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        expect(page).to_have_url(live_server.url + plan.product.get_absolute_url())
    finally:
        context.close()
    context = browser.browser_type.launch_persistent_context(profile)
    try:
        page = context.pages[0]
        page.goto(live_server.url + "/cart/")
        expect(page.locator(".cart-line")).to_have_count(1)
        expect(page.locator(".cart-line")).to_contain_text(plan.product.name)
    finally:
        context.close()

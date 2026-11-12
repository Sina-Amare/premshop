import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.catalog.models import Product

pytestmark = pytest.mark.django_db(transaction=True)


def test_guest_review_shows_selected_product_plan_quantity_and_server_total(client, plan):
    client.post(reverse("cart-add"), {"plan": plan.pk, "quantity": 2})
    response = client.get(reverse("checkout"))
    body = response.content.decode()
    assert "data-checkout-cart" in body
    assert plan.product.name in body and plan.title in body
    assert "تعداد: ۲" in body and "۴۰۰,۰۰۰" in body
    assert "پس از ورود، سبد خرید و مبلغ نهایی را بررسی می‌کنید" not in body
    assert "قیمت و موجودی هنگام ثبت سفارش دوباره بررسی می‌شوند" not in body


def test_guest_review_hides_drafts_and_excludes_unavailable_amounts(client, plan):
    client.post(reverse("cart-add"), {"plan": plan.pk})
    Product.objects.filter(pk=plan.product_id).update(status=Product.Status.DRAFT)
    body = client.get(reverse("checkout")).content.decode()
    assert "data-checkout-cart" in body and "محصول ناموجود" in body
    assert plan.product.name not in body and plan.title not in body and "۲۰۰,۰۰۰" not in body


def test_empty_guest_review_has_an_explicit_empty_state(client):
    body = client.get(reverse("checkout")).content.decode()
    assert "data-checkout-cart" in body and "سبد خرید شما خالی است" in body
    assert "۰ محصول" not in body


def test_a_guest_signs_in_at_checkout_by_code_only(client, plan):
    client.post(reverse("cart-add"), {"plan": plan.pk})
    body = client.get(reverse("checkout")).content.decode()
    assert 'name="phone"' in body
    assert 'type="password"' not in body


def test_already_signed_in_code_response_uses_safe_json_destination(client, buyer):
    client.force_login(buyer)
    response = client.post(
        reverse("login-code"), {"next": "/checkout/"}, HTTP_ACCEPT="application/json"
    )
    assert response.json() == {"ok": True, "next": "/checkout/"}


@pytest.mark.browser
@pytest.mark.parametrize("authenticated", [False, True])
def test_cart_polling_never_replaces_checkout_or_discards_typed_values(
    browser, live_server, buyer, plan, authenticated
):
    client = Client()
    if authenticated:
        client.force_login(buyer)
    client.post(reverse("cart-add"), {"plan": plan.pk})
    context = browser.new_context()
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    polls = []
    page.on(
        "request",
        lambda request: polls.append(request.url) if "/cart/availability/" in request.url else None,
    )
    try:
        page.clock.install()
        page.goto(live_server.url + reverse("checkout"))
        summary = page.locator(".cart-summary")
        expect(summary.get_by_role("link", name="ویرایش سبد خرید", exact=True)).to_be_visible()
        expect(page.locator(".cart-heading a")).to_have_count(0)
        field = (
            page.locator('[name="email"]')
            if authenticated
            else page.get_by_label("شماره‌ی موبایل", exact=True)
        )
        value = "typed@example.test" if authenticated else "09121234567"
        field.fill(value)
        page.clock.fast_forward(16000)
        page.evaluate('document.dispatchEvent(new Event("visibilitychange"))')
        page.wait_for_timeout(500)
        expect(page.get_by_role("heading", name="ثبت سفارش", exact=True)).to_be_visible()
        expect(field).to_have_value(value)
        assert not polls
    finally:
        context.close()


@pytest.mark.browser
@pytest.mark.parametrize("width", [390, 1440])
def test_long_mixed_cart_preview_keeps_controls_and_prices_within_viewport(
    browser, live_server, plan, width, tmp_path
):
    plan.product.name = "اشتراک ابزار برنامه‌نویسی GitHub Copilot Enterprise برای تیم‌های بزرگ"
    plan.product.image = "products/checkout-mark.svg"
    plan.product.brand_colour = "#d97757"
    plan.product.save(update_fields=["name", "image", "brand_colour"])
    plan.title = "یک‌ساله؛ " + "LongUnbrokenPlanName" * 4
    plan.sale_price = 999999999999
    plan.save(update_fields=["title", "sale_price"])
    client = Client()
    client.post(reverse("cart-add"), {"plan": plan.pk, "quantity": 2})
    context = browser.new_context(viewport={"width": width, "height": 1000})
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.session.session_key,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    try:
        page.route(
            "**/media/products/checkout-mark.svg",
            lambda route: route.fulfill(
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
                '<circle cx="20" cy="20" r="18" fill="#d97757"/></svg>',
            ),
        )
        page.goto(live_server.url + reverse("checkout"))
        expect(page.locator("[data-checkout-cart]")).to_contain_text(plan.title)
        expect(page.get_by_role("button", name="دریافت کد ورود", exact=True)).to_be_visible()
        page.evaluate("document.fonts.ready")
        art = page.locator(".checkout-preview__art .pcard__art")
        image = art.locator("img")
        expect(image).to_be_visible()
        assert image.bounding_box()["width"] >= 28
        assert art.evaluate("el => getComputedStyle(el).backgroundColor") != "rgba(0, 0, 0, 0)"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / f"long-{width}.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.browser
def test_inline_signin_disables_the_visible_submit_while_request_is_pending(browser, live_server):
    context = browser.new_context()
    page = context.new_page()
    held = []
    try:
        page.goto(live_server.url + reverse("checkout"))
        page.route("**/login/code/", lambda route: held.append(route))
        form = page.locator("[data-inline-phone]")
        form.get_by_label("شماره‌ی موبایل", exact=True).fill("09121234567")
        submit = form.locator('[type="submit"]')
        submit.click()
        expect(form).to_have_attribute("aria-busy", "true")
        expect(submit).to_be_disabled()
        assert len(held) == 1
        held[0].fulfill(status=400, json={"ok": False, "error": "این ایمیل درست نیست."})
        expect(submit).to_be_enabled()
        expect(form).not_to_have_attribute("aria-busy", "true")
    finally:
        context.close()

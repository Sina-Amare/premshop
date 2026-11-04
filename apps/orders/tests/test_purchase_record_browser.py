import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.accounts.models import User
from apps.catalog.models import Product, ProductSection
from apps.cms.models import Page
from apps.orders.models import Order
from apps.orders.services import OrderLine, place_order
from apps.orders.tests.test_customer_browser import context_for
from apps.orders.tests.test_purchase_record import offer as offer
from apps.orders.tests.test_purchase_record import policies as policies

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def fonts_in_place(page):
    """Text reflows when the web fonts arrive, so widths are measured after."""
    # Layout first, so a face only the just-opened text uses has started loading.
    page.evaluate("document.body.getBoundingClientRect(), document.fonts.ready.then(() => true)")


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("javascript", [True, False])
def test_saved_customer_and_panel_conditions_are_readable(
    browser, live_server, buyer, offer, policies, width, javascript, tmp_path
):
    prose = "### عنوان داخلی\n\n" + "شرایط استفاده از حساب Example Pro با گارانتی ثبت‌شده.\n\n" * 8
    Product.objects.filter(pk=offer.product_id).update(full_description=prose)
    ProductSection.objects.filter(product=offer.product).update(
        body="| ویژگی | شرایط |\n| --- | --- |\n| حساب | شخصی Example Pro |\n\n"
        "- یک نکته برای استفاده\n- یک نکته برای جایگزینی"
    )
    Page.objects.filter(slug__in=policies).update(
        body="## شرایط ثبت‌شده\n\n" + prose + '\n<script>alert("unsafe")</script>'
    )
    order = place_order(user=buyer, lines=[OrderLine(offer, 1)])
    operator = User.objects.create_superuser("operator@example.test")
    for role, account, path in (
        ("customer", buyer, reverse("order-detail", args=[order.order_number])),
        ("panel", operator, reverse("admin:orders_order_change", args=[order.pk])),
    ):
        context = context_for(browser, live_server, account, width, javascript=javascript)
        page = context.new_page()
        try:
            page.goto(live_server.url + path)
            expect(page).to_have_url(live_server.url + path)
            expect(page.get_by_text("شرایط زمان خرید", exact=True).first).to_be_visible()
            expect(page.locator(".saved-product")).to_have_count(1)
            if role == "customer":
                assert page.locator(".saved-product").evaluate(
                    "el => el.clientWidth >= el.parentElement.clientWidth * 0.9"
                )
            page.locator(".saved-product > summary").click()
            for summary in page.locator(".saved-policy > summary").all():
                summary.click()
            expect(page.locator(".saved-product")).to_contain_text("گارانتی ثبت‌شده")
            expect(page.locator(".saved-product")).to_contain_text("نکات استفاده زمان خرید")
            expect(page.locator(".saved-product")).to_contain_text("بازگشت وجه زمان خرید")
            expect(page.locator("[data-saved-policy]")).to_have_count(3)
            assert page.locator(".saved-policy script, .saved-product script").count() == 0
            assert page.locator("html").get_attribute("dir") == "rtl"
            fonts_in_place(page)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(tmp_path / f"{role}-{width}-{javascript}.png"), full_page=True)
        finally:
            context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_older_conditions_render_without_policy_disclosures(
    browser, live_server, buyer, offer, width, tmp_path
):
    order = place_order(user=buyer, lines=[OrderLine(offer, 1)])
    order.items.update(product_snapshot={"name": "محصول قبلی", "plan_title": "پلن قبلی"})
    Order.objects.filter(pk=order.pk).update(policy_snapshot={})
    operator = User.objects.create_superuser("operator@example.test")
    for role, account, path in (
        ("customer", buyer, reverse("order-detail", args=[order.order_number])),
        ("panel", operator, reverse("admin:orders_order_change", args=[order.pk])),
    ):
        context = context_for(browser, live_server, account, width, javascript=False)
        page = context.new_page()
        try:
            page.goto(live_server.url + path)
            expect(page).to_have_url(live_server.url + path)
            expect(page.locator(".saved-product")).to_have_count(1)
            page.locator(".saved-product > summary").click()
            assert page.locator("[data-saved-policy]").count() == 0
            assert "توضیح کامل زمان خرید" not in page.content()
            fonts_in_place(page)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(tmp_path / f"{role}-older-{width}.png"), full_page=True)
        finally:
            context.close()

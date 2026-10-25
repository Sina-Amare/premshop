"""The read-only plan dialog remains usable while prices expire."""

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from apps.catalog.models import Category, Plan, Product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.fixture
def service():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category, name="Service Plus", slug="service-plus", status="active"
    )
    Plan.objects.create(product=product, title="۱ ماهه", cost_price=1, sale_price=200000)
    Plan.objects.create(
        product=product,
        title="۳ ماهه",
        cost_price=1,
        sale_price=600000,
        promo_price=500000,
        promo_ends_at=timezone.now() + timedelta(minutes=10),
        sort_order=1,
    )
    return product


@pytest.mark.parametrize("width", [390, 1440])
def test_dialog_escape_backdrop_focus_and_mobile_bounds(browser, live_server, service, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(live_server.url + "/c/ai/")
        trigger = page.locator("[data-quick-open]").first
        trigger.click()
        dialog = page.get_by_role("dialog", name="Service Plus")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("۵۰۰,۰۰۰")
        expect(dialog.locator("[data-quick-close]")).to_be_focused()
        assert dialog.bounding_box()["height"] <= 900 * 0.85 + 1
        page.keyboard.press("Shift+Tab")
        assert dialog.evaluate("e=>e.contains(document.activeElement)")
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        trigger.click()
        page.mouse.click(2, 2)
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    finally:
        page.close()


@pytest.mark.parametrize("expire_before_open", [False, True])
def test_expiry_updates_the_original_preview_dom(browser, live_server, service, expire_before_open):
    service.plans.filter(title="۳ ماهه").update(promo_ends_at=timezone.now() + timedelta(seconds=4))
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(live_server.url + "/c/ai/")
        if expire_before_open:
            page.wait_for_timeout(4200)
        page.locator("[data-quick-open]").first.click()
        dialog = page.locator("#quick-plans")
        row = dialog.locator("[data-quick-product] li").filter(has_text="۳ ماهه")
        if not expire_before_open:
            expect(row).to_contain_text("۵۰۰,۰۰۰")
        expect(row.locator(".price__num")).to_have_text("۶۰۰,۰۰۰", timeout=6000)
        expect(row.locator("del")).to_have_count(0)
    finally:
        page.close()


def test_long_names_and_maximum_discount_prices_fit_a_small_phone(browser, live_server, service):
    service.name = "LongServiceName" * 8
    service.save(update_fields=["name"])
    service.plans.filter(title="۳ ماهه").update(sale_price=999999999999, promo_price=999999999998)
    page = browser.new_page(viewport={"width": 320, "height": 700})
    try:
        page.goto(live_server.url + "/c/ai/")
        page.locator("[data-quick-open]").first.click()
        assert page.locator("#quick-plans").evaluate("""e=>{
          const bounds=e.getBoundingClientRect();
          return [...e.querySelectorAll('h2,.price,.price__num,.price__cur,del')]
            .filter(n=>n.getClientRects().length).every(n=>{
              const r=n.getBoundingClientRect();
              return r.left>=bounds.left && r.right<=bounds.right;
            });
        }""")
    finally:
        page.close()


def test_no_javascript_keeps_the_product_link_and_hides_dialog_actions(
    browser, live_server, service
):
    page = browser.new_page(viewport={"width": 390, "height": 844}, java_script_enabled=False)
    try:
        page.goto(live_server.url + "/c/ai/")
        expect(page.locator("[data-quick-open]")).to_be_hidden()
        expect(page.locator("#quick-plans")).to_be_hidden()
        page.locator(".pcard__link").first.click()
        expect(page).to_have_url(live_server.url + "/p/service-plus/")
    finally:
        page.close()

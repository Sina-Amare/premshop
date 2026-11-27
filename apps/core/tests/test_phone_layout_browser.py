"""The pages a customer sees, at phone widths, read by a detector in a real Chromium.

On a phone nothing is cut short, nothing is drawn over text, and no coloured band stops
short of its card's edge (owner, 2026-11-27). `phone_layout.js` finds each of those on the
rendered page. The first test runs it over every customer-facing page at the four common
phone widths, in the states that change the layout; the second plants one defect of each
kind and proves the detector reports it, so a clean run means a clean page.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from django.utils import timezone
from playwright.sync_api import expect

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product
from apps.cms.models import Page
from apps.core.tests.test_accessibility_browser import STYLED, context_for
from apps.orders import services
from apps.orders.tests.test_delivery import deliver
from apps.orders.tests.test_paid_cancellation import paid_order

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

DETECT = Path(__file__).with_name("phone_layout.js").read_text(encoding="utf-8")
WIDTHS = (320, 360, 390, 414)
# Four departments fill the home page's tiles; the long names are the ones that wrap.
SHOP = (
    ("ai", "هوش مصنوعی", ("ChatGPT Plus", "Claude Pro")),
    ("dev", "ابزار توسعه", ("JetBrains All Products Pack", "GitHub Copilot")),
    ("media", "سرگرمی", ("YouTube Premium", "Spotify Premium")),
    ("gift", "گیفت کارت", ("Apple Gift Card", "PlayStation Gift Card")),
)
VISITOR = {
    "home": "/",
    "department": "/c/dev/",
    "product with a long name": "/p/jetbrains-all-products-pack/",
    "search, a hit": "/search/?q=chatgpt",
    "search, a miss": "/search/?q=netflix",
    "faq": "/faq/",
    "terms": "/terms/",
    "code request": "/login/code/",
    "404": "/no-such-page/",
}
CUSTOMER = {
    "account": "/account/",
    "orders": "/account/orders/",
    "invoice": "/account/orders/{order}/invoice/",
    "tracking": "/track/{token}/",
}


@pytest.fixture
def shop():
    for slug, name, products in SHOP:
        category = Category.objects.create(name=name, slug=slug)
        for title in products:
            product = Product.objects.create(
                category=category,
                name=title,
                slug=title.lower().replace(" ", "-"),
                delivery_type=Product.DeliveryType.READY_ACCOUNT,
                warranty=Product.Warranty.FULL_PERIOD,
                delivery_hours=24,
                status=Product.Status.ACTIVE,
            )
            Plan.objects.create(
                product=product,
                title="۱ ماهه",
                duration_days=30,
                cost_price=Decimal(400000),
                sale_price=Decimal(650000),
                input_type=Plan.InputType.NONE,
                telegram_required=False,
            )
    # A published page of prose: a heading, a long paragraph, a list and a link to support.
    # The seeded row is gone once an earlier test in this worker emptied the database.
    Page.objects.update_or_create(
        slug=Page.Slug.TERMS,
        defaults={
            "title": "قوانین خرید",
            "description": "شرایط خرید، تحویل و بازگشت وجه در پرم‌شاپ.",
            "is_published": True,
            "body": (
                "## پیش از خرید\n\nپیش از پرداخت، مدت اشتراک، زمان تحویل و شرایط گارانتی هر محصول"
                " را در صفحه‌ی همان محصول ببینید. اگر سؤالی دارید، [از پشتیبانی بپرسید]"
                "(https://t.me/premshop_support).\n\n"
                "- تحویل تا ۴۸ ساعت پس از تأیید پرداخت\n- لغو سفارش و بازگشت وجه پیش از تحویل"
            ),
        },
    )
    return Product.objects.get(slug="chatgpt-plus")


def look(page) -> list[str]:
    page.evaluate("document.fonts.ready")
    assert page.evaluate(STYLED), f"a stylesheet on {page.url} did not load"
    return page.evaluate(DETECT)


def visit(page, url: str) -> list[str]:
    response = page.goto(url)
    # A redirect to sign-in would otherwise be read under the name of the page it replaced.
    expected = 404 if url.endswith("/no-such-page/") else 200
    assert (response.status, page.url) == (expected, url), f"{url} -> {page.url}"
    return look(page)


def test_no_customer_page_cuts_or_covers_text_at_phone_widths(browser, live_server, shop, settings):
    # The delivery below encrypts its details, so the test brings its own key.
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEY_FALLBACKS = []
    customer = User.objects.create_user(
        "customer@example.test", phone="09121230001", is_verified=True
    )
    operator = User.objects.create_superuser("operator@example.test")
    order, items = paid_order(customer, operator, [services.OrderLine(shop.plans.get(), 1)])
    deliver(items[0], operator, expires_at=timezone.localdate() + timedelta(days=30))
    numbers = {"order": order.order_number, "token": order.tracking_token}
    found: dict[str, list[str]] = {}

    def record(name: str, width: int, findings: list[str]) -> None:
        if findings:
            found[f"{name} at {width}px"] = findings

    for width in WIDTHS:
        context = context_for(browser, live_server, width, None)
        page = context.new_page()
        try:
            for name, path in VISITOR.items():
                record(name, width, visit(page, live_server.url + path))
            visit(page, live_server.url + "/")
            page.locator(".v-mobile-cats summary").first.click()
            expect(page.locator(".v-mobile-cats[open]").first).to_be_visible()
            record("home, department menu open", width, look(page))
            visit(page, live_server.url + "/")
            page.locator(".v-search input").fill("chat")
            expect(page.locator("#search-suggest")).to_be_visible()
            record("home, search suggestions", width, look(page))
            visit(page, live_server.url + "/p/chatgpt-plus/")
            page.locator(".buybox label:has(input[type=radio])").first.click()
            page.locator(".buybox button[type=submit]:visible").first.click()
            # The product is added in the background; the header's count shows when it is in.
            expect(page.locator(".v-cart__count")).to_be_visible()
            visit(page, live_server.url + "/cart/")
            expect(page.locator(".cart-line")).to_have_count(1)
            record("cart with a product", width, look(page))
        finally:
            context.close()
        context = context_for(browser, live_server, width, customer)
        page = context.new_page()
        try:
            for name, path in CUSTOMER.items():
                record(name, width, visit(page, live_server.url + path.format(**numbers)))
            visit(page, f"{live_server.url}/account/orders/{order.order_number}/")
            # Every fold open: the replacement request, support, the saved terms, the history.
            page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
            record("delivered order, every fold open", width, look(page))
        finally:
            context.close()

    assert not found, found


PLANTED = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<style>body{margin:0;font:16px sans-serif} p{margin:0}</style></head><body>
<div style="width:300px;padding:10px;border:1px solid #000">
  <div style="width:100%;margin:0 -10px;background:#2459d3;color:#fff">نوار کارت</div></div>
<div style="width:60px;overflow:hidden;white-space:nowrap">متنی که از جعبه‌اش بیرون می‌زند</div>
<input placeholder="جستجوی همه‌ی محصولات فروشگاه" style="width:80px;font-size:16px">
<a href="#top" style="display:inline-block;width:50px;background:#eee">ورود و ثبت‌نام</a>
<div style="position:relative;width:200px"><p>متن زیرین</p>
  <span style="position:absolute;inset-inline-start:0;top:0">متن رویی</span></div>
<div style="position:relative;width:200px"><p>متنی با یک نشان</p>
  <svg width="40" height="40" style="position:absolute;inset-inline-start:0;top:0"><circle cx="20" cy="20" r="18"/></svg></div>
<div style="width:900px;height:10px"></div>
</body></html>"""


def test_the_detector_reports_each_kind_of_defect(browser):
    page = browser.new_page(viewport={"width": 390, "height": 900})
    try:
        page.set_content(PLANTED)
        found = page.evaluate(DETECT)
    finally:
        page.close()

    for kind in (
        "band short of its card's",
        "text cut off",
        "field too narrow",
        "label broken over two lines",
        "text over text",
        "icon over text",
        "page wider than the screen",
    ):
        assert any(finding.startswith(kind) for finding in found), (kind, found)

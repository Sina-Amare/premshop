"""The product page in a real Chromium: where the answers index lands.

Sections below the first are laid out only as they come near the view
(content-visibility), so a jump measured against their placeholder sizes landed
up to a screen away from its answer: above the header on a laptop, short of the
section on a phone. A layout is not something the test client can see.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.catalog.models import Category, Plan, Product, ProductSection
from apps.core.tests.test_accessibility_browser import STYLED

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

LONG = "\n".join(
    f"- **فایده‌ی {n}.** " + "جمله‌ای که چند خط از صفحه را پر می‌کند. " * 10 for n in range(1, 9)
)

# Resolves once the page has not scrolled for ten frames: a glide has ended.
SETTLED = """() => new Promise(done => {
  let last = -1, still = 0;
  const tick = () => {
    still = scrollY === last ? still + 1 : 0;
    last = scrollY;
    still > 10 ? done(scrollY) : requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})"""


@pytest.fixture
def long_page():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")

    def make(slug: str) -> Product:
        product = Product.objects.create(
            category=category,
            name=slug,
            slug=slug,
            delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
            iran_stance="پشتیبانی‌نشده",
            status=Product.Status.ACTIVE,
        )
        Plan.objects.create(
            product=product, title="۱ ماهه", cost_price=1, sale_price=Decimal(1390000)
        )
        return product

    product = make("long")
    for order, kind in enumerate(
        ["benefits", "includes", "after_payment", "free", "before_buying"]
    ):
        ProductSection.objects.create(product=product, kind=kind, body=LONG, sort_order=order)
    # Something under the questions, so the last answer can reach the top of the view.
    for slug in ("other-one", "other-two", "other-three"):
        make(slug)
    return product


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
@pytest.mark.parametrize("width", [390, 1280])
def test_every_answer_in_the_index_lands_in_view(browser, live_server, long_page, width, motion):
    """With motion welcome frame.js glides to the answer; without it the browser jumps."""
    context = browser.new_context(viewport={"width": width, "height": 800}, reduced_motion=motion)
    page = context.new_page()
    url = f"{live_server.url}/p/{long_page.slug}/"
    try:
        page.goto(url)
        assert page.evaluate(STYLED)
        rows = page.locator(".facts a.facts__row")
        hrefs = [rows.nth(i).get_attribute("href") for i in range(rows.count())]
        assert len(hrefs) == 4, hrefs  # after payment, Iran, delivery, warranty
        for i, href in enumerate(hrefs):
            page.goto(url)
            rows.nth(i).click()
            page.evaluate(SETTLED)
            header = page.evaluate(
                "(() => { const h = document.querySelector('.site-header');"
                " return getComputedStyle(h).position === 'sticky' ? h.getBoundingClientRect().bottom : 0 })()"
            )
            top = page.evaluate(f"document.querySelector('{href}').getBoundingClientRect().top")
            assert header <= top <= header + 40, (href, top, header)
    finally:
        context.close()


@pytest.fixture
def gift_card():
    category = Category.objects.create(name="گیفت کارت", slug="gift")
    product = Product.objects.create(
        category=category,
        name="Apple Gift Card",
        slug="apple-gift-card",
        delivery_type=Product.DeliveryType.GIFT_CARD,
        iran_stance="موضوعیت ندارد",
        status=Product.Status.ACTIVE,
    )
    for order, (title, price) in enumerate([("۱۰ دلاری", 1180000), ("۲۰ دلاری", 2300000)]):
        Plan.objects.create(
            product=product,
            title=title,
            cost_price=1,
            sale_price=Decimal(price),
            max_quantity=5,
            sort_order=order,
        )
    return product


def test_a_plan_change_leaves_the_quantity_alone(browser, live_server, gift_card):
    """Every change wrote the quantity back, unchanged or not, so a plan change
    also rewrote the «۱» in the stepper."""
    page = browser.new_page()
    try:
        page.goto(f"{live_server.url}/p/{gift_card.slug}/")
        page.evaluate(
            "() => { window.writes = 0; new MutationObserver(r => { window.writes += r.length; })"
            ".observe(document.querySelector('.stepper__value'),"
            " {childList: true, characterData: true, subtree: true}); }"
        )
        page.locator("label.plan").nth(1).click()
        assert page.locator(".invoice__amount").inner_text() == "۲,۳۰۰,۰۰۰"
        assert page.evaluate("window.writes") == 0
        page.locator(".stepper__btn[data-step='1']").click()
        assert page.locator(".stepper__value").inner_text() == "۲"
        assert page.evaluate("window.writes") > 0, "the control: the observer sees a change"
    finally:
        page.close()

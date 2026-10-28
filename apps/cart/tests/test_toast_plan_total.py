"""The add-to-cart toast names the whole plan and states the cart's own total."""

import re
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import expect

from apps.cart import services
from apps.catalog.models import Category, Plan, Product

pytestmark = pytest.mark.django_db

SHOTS = Path("screenshots/toast-total")
# media/ is not in the repository, so a browser test serves the logo it names.
LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
    '<circle cx="12" cy="12" r="12" fill="#1db954"/></svg>'
)
# The rounded top of the glyphs from start to end of an element's text, which tells its lines apart.
TOP = """(el, [start, end]) => {
  const range = document.createRange();
  range.setStart(el.firstChild, start);
  range.setEnd(el.firstChild, end);
  return Math.round(range.getClientRects()[0].top - el.getBoundingClientRect().top);
}"""


def _add(client, plan):
    response = client.post("/cart/add/", {"plan": plan.pk}, HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    return response.json()


def _figure(html):
    return re.search(r'<span class="price__num">([^<]+)</span>', html)[1]


def _summary_label(client):
    body = client.get("/cart/").content.decode()
    return body.split('<div class="cart-summary__label">', 1)[1].split("</div>", 1)[0]


@pytest.fixture
def plain(plan):
    product = Product.objects.create(
        category=plan.product.category,
        name="سرویس دوم",
        slug="second",
        status=Product.Status.ACTIVE,
    )
    return Plan.objects.create(product=product, title="سه ماهه", cost_price=1, sale_price=190000)


def test_each_addition_reports_the_whole_carts_total(client, plan, plain):
    Plan.objects.filter(pk=plan.pk).update(sale_price=490000, promo_price=420000)
    assert _add(client, plan)["total"] == "جمع سبد خرید: ۴۲۰,۰۰۰\u00a0تومان"
    assert _add(client, plain)["total"] == "جمع سبد خرید: ۶۱۰,۰۰۰\u00a0تومان"
    assert _summary_label(client) == "جمع سبد خرید"


def test_a_row_that_needs_fixing_leaves_its_amount_out_under_the_cart_pages_label(
    client, plan, plain
):
    _add(client, plain)
    Plan.objects.filter(pk=plain.pk).update(is_available=False)
    total = _add(client, plan)["total"]
    assert total == "جمع محصولات قابل خرید: ۲۰۰,۰۰۰\u00a0تومان"
    assert total.startswith(_summary_label(client) + ": ")


def test_a_cart_with_nothing_left_to_buy_states_no_total(client, plan, monkeypatch):
    """Sold out between the addition and the read, «رایگان» would be a false figure."""
    add = services.add_to_cart

    def add_then_sell_out(request, **kwargs):
        item = add(request, **kwargs)
        Plan.objects.filter(pk=plan.pk).update(stock_quantity=0)
        return item

    monkeypatch.setattr(services, "add_to_cart", add_then_sell_out)
    data = _add(client, plan)
    assert data["title"] == "به سبد خرید اضافه شد"
    assert "total" not in data


def test_quantity_changes_and_removals_leave_the_total_to_the_cart_page(client, plan):
    _add(client, plan)
    for url, data in (
        ("/cart/quantity/", {"plan": plan.pk, "quantity": 2}),
        ("/cart/remove/", {"plan": plan.pk}),
    ):
        response = client.post(url, data, HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert "total" not in response.json()


def test_the_plan_row_the_cart_line_and_the_toast_state_one_figure(plan):
    Plan.objects.filter(pk=plan.pk).update(sale_price=490000, promo_price=420000)
    plain = Plan.objects.create(
        product=plan.product, title="سه ماهه", cost_price=1, sale_price=190000, sort_order=1
    )
    free = Plan.objects.create(
        product=plan.product, title="آزمایشی", cost_price=0, sale_price=0, sort_order=2
    )
    figures = {}
    for each in (plan, plain, free):
        client = Client()
        page = client.get(plan.product.get_absolute_url()).content.decode()
        row = page.split(f'name="plan" value="{each.pk}"', 1)[1].split("</label>", 1)[0]
        total = _add(client, each)["total"]
        line = client.get("/cart/").content.decode().split('class="cart-line__amount"', 1)[1]
        assert total.startswith("جمع سبد خرید: ")
        figures[each.title] = {
            _figure(row.split("data-plan-price", 1)[1]),
            _figure(line),
            total.removeprefix("جمع سبد خرید: ").removesuffix("\u00a0تومان"),
        }
    assert figures == {"یک ماهه": {"۴۲۰,۰۰۰"}, "سه ماهه": {"۱۹۰,۰۰۰"}, "آزمایشی": {"رایگان"}}


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width, height", [(1440, 900), (390, 844), (360, 740)])
def test_the_toast_shows_the_whole_plan_and_the_total(browser, live_server, width, height):
    category = Category.objects.create(name="موسیقی", slug="music")
    # A logo narrows the toast's text column, as the real product's does.
    product = Product.objects.create(
        category=category,
        name="Spotify Premium",
        slug="spotify",
        status=Product.Status.ACTIVE,
        image="products/spotify.svg",
    )
    Plan.objects.create(
        product=product, title="۳ ماهه", cost_price=1, sale_price=490000, promo_price=420000
    )
    SHOTS.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={"width": width, "height": height})
    page.route(
        f"**{product.image.url}",
        lambda route: route.fulfill(content_type="image/svg+xml", body=LOGO),
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    toast = page.locator("#cart-toast")
    try:
        page.goto(live_server.url + product.get_absolute_url())
        expect(page.locator("h1")).to_have_text("Spotify Premium")
        if width < 720:
            # Past the buy box, the phone's buy bar submits the box's form.
            page.evaluate(
                "() => scrollTo({top: document.querySelector('.buybox__cta')"
                ".getBoundingClientRect().bottom + scrollY + 40, behavior: 'instant'})"
            )
            page.get_by_role("region", name="خرید سریع").get_by_role("button").click()
        else:
            page.locator(".buybox__cta").click()
        expect(toast).to_be_visible()
        # A broken logo raises no page error, so the image is checked as loaded.
        image = toast.locator("[data-toast-image]")
        expect(image).to_have_js_property("complete", True)
        assert image.evaluate("img => img.naturalWidth") > 0
        page.evaluate("document.fonts.ready")
        expect(toast.locator("[data-toast-title]")).to_have_text("به سبد خرید اضافه شد")
        detail = toast.locator("[data-toast-detail]")
        expect(detail).to_have_text("Spotify Premium، ۳ ماهه")
        assert detail.evaluate("el => el.scrollWidth <= el.clientWidth")
        total = toast.locator("[data-toast-total]")
        expect(total).to_be_visible()
        expect(total).to_have_text("جمع سبد خرید: ۴۲۰,۰۰۰\u00a0تومان")
        expect(page.locator("#cart-announcement")).to_have_text(
            "Spotify Premium، ۳ ماهه به سبد خرید اضافه شد."
        )
        box = toast.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width
        assert box["y"] >= 0 and box["y"] + box["height"] <= height
        if width >= 720:
            assert box["width"] == 380
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # Beside a logo the text column is too narrow for the total, so it has a row of its own:
        # one line, which keeps the toast short and, on a wide screen, clear of the plans.
        end = len(total.text_content())
        assert total.evaluate(TOP, [0, 1]) == total.evaluate(TOP, [end - 1, end])
        assert box["height"] <= 140
        if width >= 720:
            plan_row = page.locator("#buy-form .plan").first.bounding_box()
            assert box["y"] + box["height"] <= plan_row["y"]
        page.screenshot(path=str(SHOTS / f"{width}.png"))
        # However the line wraps, the figure keeps its unit beside it: narrowed until it breaks
        # at every space it may, with no `text-wrap: pretty`, which Safari before 26 lacks.
        total.evaluate("el => el.style.cssText = 'text-wrap: wrap; inline-size: min-content'")
        unit = total.text_content().index("تومان")
        assert total.evaluate(TOP, [unit - 2, unit - 1]) == total.evaluate(TOP, [unit, unit + 1])
        assert not errors
    finally:
        page.close()

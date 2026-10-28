"""A plan picked in the quick-plans sheet arrives chosen on the product page."""

import re

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.catalog.models import Plan, Product
from apps.catalog.tests.test_cards import offers as offers

pytestmark = pytest.mark.django_db


def _page(client, product, plan=None):
    response = client.get(
        reverse("product", args=[product.slug]), {} if plan is None else {"plan": plan}
    )
    assert response.status_code == 200
    return response.content.decode()


def _checked(body):
    radios = re.findall(r'<input type="radio" name="plan" value="(\d+)"([^>]*)>', body)
    return [int(value) for value, attrs in radios if re.search(r"\schecked\b", attrs)]


def _summary(body):
    """The buy box summary as the server sent it: (hidden, title, price)."""
    hidden = bool(
        re.search(r"\bhidden\b", re.search(r'class="buybox__selection"([^>]*)>', body)[1])
    )
    title = re.search(r"<bdi data-selected-title>(.*?)</bdi>", body)[1]
    price = re.search(r"data-selected-price>(.*?)</span></div>", body, re.S)[1]
    return hidden, title, " ".join(re.sub(r"<[^>]+>", " ", price).split())


def _hidden(body, marker):
    return bool(re.search(r"\bhidden\b", re.search(rf"{marker}([^>]*)>", body)[1]))


def test_a_plan_named_in_the_address_is_chosen_on_first_paint(client, offers):
    first, longer = offers.plans.order_by("sort_order")
    assert _checked(_page(client, offers)) == [first.pk]
    body = _page(client, offers, longer.pk)
    assert _checked(body) == [longer.pk]
    assert _summary(body) == (False, "۳ ماهه", "۳,۷۸۰,۰۰۰ تومان")


def test_without_a_choice_the_summary_names_the_first_plan(client, offers):
    assert _summary(_page(client, offers)) == (False, "۱ ماهه", "۱,۴۵۰,۰۰۰ تومان")
    offers.plans.filter(title="۳ ماهه").delete()
    # One option needs no summary repeating it.
    assert _summary(_page(client, offers))[0]


@pytest.mark.parametrize(
    "kind", ["letters", "empty", "decimal", "negative", "huge", "foreign", "hidden", "sold_out"]
)
def test_any_other_plan_value_keeps_the_default_choice(client, offers, kind):
    first, longer = offers.plans.order_by("sort_order")
    value = {
        "letters": "abc",
        "empty": "",
        "decimal": f"{longer.pk}.0",
        "negative": f"-{longer.pk}",
        "huge": "9" * 5000,
    }.get(kind, longer.pk)
    if kind == "foreign":
        other = Product.objects.create(
            category=offers.category, name="Other", slug="other", status=Product.Status.ACTIVE
        )
        value = Plan.objects.create(product=other, title="۶ ماهه", cost_price=1, sale_price=9).pk
    elif kind == "hidden":
        offers.plans.filter(pk=longer.pk).update(is_available=False)
    elif kind == "sold_out":
        offers.plans.filter(pk=longer.pk).update(stock_quantity=0)
    body = _page(client, offers, value)
    assert _checked(body) == [first.pk]
    assert _summary(body)[1] == "۱ ماهه"


def test_the_buy_box_actions_follow_the_chosen_plan(client, offers):
    first, longer = offers.plans.order_by("sort_order")
    offers.plans.filter(pk=longer.pk).update(max_quantity=3)
    client.post(reverse("cart-add"), {"plan": first.pk})  # the first plan is now at its maximum
    body = _page(client, offers, longer.pk)
    assert not _hidden(body, "buybox__cta") and _hidden(body, "buybox__cart")
    assert not _hidden(body, 'id="qty-row"')
    assert re.search(r'id="qty"[^>]*max="3"', body)
    body = _page(client, offers)
    assert _hidden(body, "buybox__cta") and not _hidden(body, "buybox__cart")
    assert _hidden(body, 'id="qty-row"')


def test_the_canonical_address_leaves_the_plan_out(client, offers):
    body = _page(client, offers, offers.plans.get(title="۳ ماهه").pk)
    canonical = re.search(r'<link rel="canonical" href="([^"]*)">', body)[1]
    assert canonical.endswith(reverse("product", args=[offers.slug])) and "?" not in canonical
    assert re.search(r'<meta property="og:url" content="([^"]*)">', body)[1] == canonical


def test_sheet_rows_link_to_their_plan_and_unavailable_rows_stay_text(client, offers):
    first, longer = offers.plans.order_by("sort_order")
    offers.plans.create(title="پلن قدیمی", cost_price=1, sale_price=9, is_available=False)
    offers.plans.create(title="۶ ماهه", cost_price=1, sale_price=9, stock_quantity=0)
    body = client.get(reverse("home")).content.decode()
    pool = body.split('<dialog id="quick-plans"', 1)[1].split("</dialog>", 1)[0]
    rows = re.findall(r"<li>.*?</li>", pool, re.S)

    def row(title):
        return next(item for item in rows if title in item)

    url = reverse("product", args=[offers.slug])
    for plan in (first, longer):
        assert re.findall(r'<a [^>]*href="([^"]*)"', row(plan.title)) == [f"{url}?plan={plan.pk}"]
    assert "<a " not in row("پلن قدیمی") and "<a " not in row("۶ ماهه")


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [390, 1440])
def test_a_tapped_sheet_row_lands_on_the_product_with_that_plan(
    browser, live_server, offers, width
):
    longer = offers.plans.get(title="۳ ماهه")
    page = browser.new_page(viewport={"width": width, "height": 844})
    try:
        page.goto(live_server.url + reverse("home"))
        page.locator(f'[data-quick-open="{offers.pk}"]').first.click()
        dialog = page.get_by_role("dialog", name="Claude Pro")
        dialog.get_by_role("link", name=re.compile("۳ ماهه")).click()
        expect(page).to_have_url(f"{live_server.url}/p/{offers.slug}/?plan={longer.pk}")
        expect(page.locator(f'input[name=plan][value="{longer.pk}"]')).to_be_checked()
        expect(page.locator("[data-selected-title]")).to_have_text("۳ ماهه")
        expect(page.locator("[data-selected-price] .price__num")).to_have_text("۳,۷۸۰,۰۰۰")
        expect(page.locator(".buybox__cta")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    finally:
        page.close()

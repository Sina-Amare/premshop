"""A plan of two months or more says what it comes to a month, and how much
less that is than buying the one-month plan each month."""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from playwright.sync_api import expect

from apps.catalog import pricing
from apps.catalog.models import Category, Plan, Product
from apps.catalog.templatetags import prices


def plan(days, price, **fields):
    return Plan(duration_days=days, sale_price=Decimal(price), cost_price=Decimal(1), **fields)


def test_three_months_at_420000_is_140000_a_month():
    three = plan(90, 420000)
    assert pricing.monthly_price(three, [three]) == (140000, None)


def test_against_a_one_month_plan_at_160000_the_saving_is_12_percent():
    three, month = plan(90, 420000), plan(30, 160000)
    assert pricing.monthly_price(three, [month, three]) == (140000, 12)


def test_promoted_prices_are_the_ones_divided_and_compared():
    three = plan(90, 480000, promo_price=Decimal(420000))
    month = plan(30, 200000, promo_price=Decimal(160000))
    assert pricing.monthly_price(three, [month, three]) == (140000, 12)


def test_a_saving_under_one_percent_is_not_shown():
    month = plan(30, 160000)
    assert pricing.monthly_price(plan(90, 477000), [month]) == (159000, None)
    assert pricing.monthly_price(plan(90, 500000), [month]) == (167000, None)


def test_a_plan_of_no_duration_or_of_under_two_months_gives_nothing():
    month = plan(30, 160000)
    assert pricing.monthly_price(plan(None, 500000), [month]) is None
    assert pricing.monthly_price(month, [month]) is None
    assert pricing.monthly_price(plan(59, 320000), [month]) is None
    assert pricing.monthly_price(plan(60, 320000), [month]) == (160000, None)


def test_twelve_months_round_to_the_nearest_thousand_toman():
    year = plan(365, 1390000)  # 115,833 a month
    assert pricing.monthly_price(year, [year]).price == 116000
    assert pricing.monthly_price(plan(365, 1383000), []).price == 115000  # 115,250, not up


def test_the_saving_is_against_the_cheapest_purchasable_one_month_plan():
    three = plan(90, 420000)
    for gone in (plan(30, 160000, is_available=False), plan(30, 160000, stock_quantity=0)):
        assert pricing.monthly_price(three, [three, gone]) == (140000, None)
    others = [plan(30, 160000), plan(31, 150000), plan(40, 100000)]
    assert pricing.monthly_price(three, [three, *others]) == (140000, 6)


def test_the_line_after_a_discount_ends_comes_with_the_seconds_until_then():
    ends = timezone.now() + timedelta(hours=1)
    three = plan(90, 480000, promo_price=Decimal(420000), promo_ends_at=ends)
    now, after = prices.monthly_price_steps(three, [plan(30, 160000), three])
    assert now["value"] == (140000, 12) and 3590 < now["left"] <= 3600
    assert after == {"value": (160000, None), "left": None}
    assert prices.monthly_price_steps(plan(30, 160000), [three]) == []


@pytest.fixture
def service():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category, name="Claude Pro", slug="claude-pro", status=Product.Status.ACTIVE
    )
    Plan.objects.create(
        product=product, title="۱ ماهه", duration_days=30, cost_price=1, sale_price=160000
    )
    return product


@pytest.mark.django_db
def test_the_three_month_row_carries_the_line_and_the_one_month_row_does_not(client, service):
    service.plans.create(
        title="۳ ماهه", duration_days=90, cost_price=1, sale_price=420000, sort_order=1
    )
    body = client.get(reverse("product", args=[service.slug])).content.decode()
    one, three = re.findall(r"<label data-preview-row.*?</label>", body, re.S)
    assert "۱ ماهه" in one and "معادل ماهی حدود" not in one
    assert "معادل ماهی حدود ۱۴۰,۰۰۰ تومان، ۱۲٪ کمتر از خرید ماهانه" in re.sub(r"<[^>]+>", "", three)


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [390, 1440])
def test_the_line_stays_in_its_row_and_follows_the_countdown(browser, live_server, service, width):
    service.plans.create(
        title="۳ ماهه",
        duration_days=90,
        cost_price=1,
        sale_price=480000,
        promo_price=420000,
        promo_ends_at=timezone.now() + timedelta(hours=1),
        sort_order=1,
    )
    service.plans.create(
        title="۶ ماهه",
        duration_days=180,
        cost_price=1,
        sale_price=840000,
        is_available=False,
        sort_order=2,
    )
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.clock.install()
        page.goto(live_server.url + reverse("product", args=[service.slug]))
        rows = page.locator("label.plan")
        line = rows.nth(1).locator(".plan__monthly")
        expect(line).to_have_text(
            "معادل ماهی حدود ۱۴۰,۰۰۰ تومان، ۱۲٪ کمتر از خرید ماهانه", use_inner_text=True
        )
        box, row = line.bounding_box(), rows.nth(1).bounding_box()
        assert row["x"] <= box["x"] and box["x"] + box["width"] <= row["x"] + row["width"]
        assert rows.nth(0).locator(".plan__monthly").count() == 0
        expect(rows.nth(2).locator(".plan__monthly")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.clock.fast_forward("01:00:01")
        expect(line).to_have_text("معادل ماهی حدود ۱۶۰,۰۰۰ تومان", use_inner_text=True)
    finally:
        page.close()


@pytest.mark.django_db
def test_the_poll_sends_the_line_again_only_when_a_figure_or_its_deadline_moved(client, service):
    month = service.plans.get()
    Plan.objects.filter(pk=month.pk).update(
        promo_price=150000, promo_ends_at=timezone.now() + timedelta(hours=1)
    )
    three = service.plans.create(
        title="۳ ماهه", duration_days=90, cost_price=1, sale_price=420000, sort_order=1
    )
    url = reverse("cart-availability") + f"?plan={month.pk}&plan={three.pk}"

    def line():
        return client.get(url).json()["options"][str(three.pk)]

    first = line()
    assert "۶٪ کمتر از خرید ماهانه" in first["monthly_html"]
    page = client.get(reverse("product", args=[service.slug])).content.decode()
    assert f'data-monthly-key="{escape(first["monthly_key"])}"' in page
    assert line()["monthly_key"] == first["monthly_key"]
    # Extended, the discount keeps the same figures, but the line must not drop it on time.
    Plan.objects.filter(pk=month.pk).update(promo_ends_at=timezone.now() + timedelta(hours=3))
    assert line()["monthly_key"] != first["monthly_key"]


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_the_poll_redraws_the_line_when_the_one_month_offer_starts_or_sells_out(
    browser, live_server, service
):
    month = service.plans.get()
    Plan.objects.filter(pk=month.pk).update(
        sale_price=200000,
        promo_price=160000,
        promo_starts_at=timezone.now() + timedelta(minutes=10),
    )
    service.plans.create(
        title="۳ ماهه", duration_days=90, cost_price=1, sale_price=420000, sort_order=1
    )
    page = browser.new_page()

    def poll():
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

    try:
        page.goto(live_server.url + reverse("product", args=[service.slug]))
        line = page.locator("label.plan").nth(1).locator(".plan__monthly")
        expect(line).to_have_text(
            "معادل ماهی حدود ۱۴۰,۰۰۰ تومان، ۳۰٪ کمتر از خرید ماهانه", use_inner_text=True
        )
        Plan.objects.filter(pk=month.pk).update(
            promo_starts_at=timezone.now() - timedelta(minutes=1)
        )
        poll()
        expect(line).to_have_text(
            "معادل ماهی حدود ۱۴۰,۰۰۰ تومان، ۱۲٪ کمتر از خرید ماهانه", use_inner_text=True
        )
        Plan.objects.filter(pk=month.pk).update(stock_quantity=0)
        poll()
        expect(page.locator("label.plan").nth(0)).to_contain_text("فعلاً ناموجود")
        expect(line).to_have_text("معادل ماهی حدود ۱۴۰,۰۰۰ تومان", use_inner_text=True)
    finally:
        page.close()

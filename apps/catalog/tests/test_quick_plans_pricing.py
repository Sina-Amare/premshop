"""The quick-plans sheet states a plan's discount and its monthly figure the way
the buy box does, and the panel keeps a discount out of the text printed beside it."""

import re
from datetime import timedelta

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.catalog.models import Category, Plan, Product
from apps.catalog.tests.test_admin import errors_under, form_of
from apps.catalog.tests.test_admin import operator as operator

pytestmark = pytest.mark.django_db

QUARTER = "معادل ماهی حدود ۱۴۰,۰۰۰ تومان، ۲۶٪ کمتر از خرید ماهانه"
QUARTER_AFTER = "معادل ماهی حدود ۱۶۳,۰۰۰ تومان، ۱۴٪ کمتر از خرید ماهانه"
REFUSAL = (
    "تخفیف را اینجا ننویسید. "
    "سایت درصد آن را از قیمت تخفیفی پلن حساب می‌کند و کنار قیمت نشان می‌دهد."
)
SECONDARY = "rgb(86, 97, 112)"  # --color-secondary on the storefront
TIME_TEXT = "rgb(146, 64, 14)"  # --color-time-text, the amber kept for time
OLD_ORANGE = "rgb(154, 52, 18)"  # #9a3412, which the sheet's offer line used


@pytest.fixture
def spotify():
    """Spotify's plans as the review database holds them, and a sold-out half year."""
    category = Category.objects.create(name="موسیقی", slug="music")
    product = Product.objects.create(
        category=category,
        name="Spotify Premium",
        slug="spotify-premium",
        status=Product.Status.ACTIVE,
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
    )
    ends = timezone.now() + timedelta(days=4, hours=5, minutes=30)
    rows = [
        ("۱ ماهه", 30, 190000, {}),
        ("۳ ماهه", 90, 490000, {"promo_price": 420000, "promo_ends_at": ends}),
        ("۶ ماهه", 180, 900000, {"stock_quantity": 0}),
        ("۱۲ ماهه", 365, 1690000, {}),
    ]
    for order, (title, days, price, fields) in enumerate(rows):
        Plan.objects.create(
            product=product,
            title=title,
            duration_days=days,
            cost_price=1,
            sale_price=price,
            sort_order=order,
            **fields,
        )
    return product


def _sheet(body):
    return body.split('<dialog id="quick-plans"', 1)[1].split("</dialog>", 1)[0]


def _row(sheet, title):
    return next(row for row in re.findall(r"<li>.*?</li>", sheet, re.S) if f">{title}<" in row)


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_a_promoted_row_carries_the_buy_boxs_neutral_badge_and_its_clock(client, spotify):
    quarter = _row(_sheet(client.get(reverse("home")).content.decode()), "۳ ماهه")
    badge = re.search(
        r'<span class="badge badge--neutral" data-offer-badge[^>]*>(.*?)</span>', quarter
    )
    assert badge and badge[1] == "۱۴٪ تخفیف"
    assert re.search(r'class="badge badge--time deadline"[^>]*>۴ روز و ۵ ساعت مانده<', quarter)
    css = (settings.BASE_DIR / "static" / "css" / "storefront.css").read_text(encoding="utf-8")
    assert "#9a3412" not in css.lower() and ".quick-plans__offer" not in css


def test_the_quarter_states_its_month_in_the_sheet_and_in_the_buy_box(client, spotify):
    body = client.get(reverse("product", args=[spotify.slug])).content.decode()
    sheet = _sheet(body)
    assert QUARTER in _text(_row(sheet, "۳ ماهه"))
    assert "معادل" not in _row(sheet, "۱ ماهه") and "معادل" not in _row(sheet, "۶ ماهه")
    box = {
        re.search(r"<bdi>(.*?)</bdi>", label)[1]: label
        for label in re.findall(r"<label data-preview-row.*?</label>", body, re.S)
    }
    assert QUARTER in _text(box["۳ ماهه"]) and "معادل" not in box["۱ ماهه"]


def test_the_sheet_still_only_links_each_plan_to_its_product_page(client, spotify):
    sheet = _sheet(client.get(reverse("home")).content.decode())
    assert "<form" not in sheet and "افزودن به سبد" not in sheet
    assert not any("<button" in link for link in re.findall(r"<a\b[^>]*>.*?</a>", sheet, re.S))
    url = reverse("product", args=[spotify.slug])
    for plan in spotify.plans.all():
        links = re.findall(r'<a [^>]*href="([^"]*)"', _row(sheet, plan.title))
        assert links == ([f"{url}?plan={plan.pk}"] if plan.is_in_stock else []), plan.title


def test_the_monthly_line_reads_the_plans_already_fetched(
    client, spotify, django_assert_num_queries
):
    with django_assert_num_queries(6):  # Includes published legal-page navigation.
        body = client.get(reverse("home")).content.decode()
    assert QUARTER in _text(_sheet(body))


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", "Spotify Premium ۱۴٪ تخفیف"),
        ("seo_title", "خرید Spotify با 20% تخفیف"),
        ("name", "Spotify Premium 20%"),
        ("seo_title", "خرید Spotify Premium با تخفیف"),
        ("name", "Spotify Premium با تخفيف"),  # typed with an Arabic keyboard's yeh
        ("seo_description", "Spotify Premium روی اکانت خودتان، با ۲۰٪ تخفیف."),
    ],
)
def test_a_discount_written_into_the_name_or_the_seo_text_is_refused_on_that_field(
    spotify, field, value
):
    setattr(spotify, field, value)
    with pytest.raises(ValidationError) as refused:
        spotify.full_clean()
    assert refused.value.message_dict == {field: [REFUSAL]}


def test_a_discount_written_into_a_plans_title_is_refused_on_that_field(spotify):
    """The sheet and the buy box print the title beside the computed badge, so
    this one stood «۳ ماهه ۲۰٪ تخفیف» next to «۱۴٪ تخفیف»."""
    quarter = spotify.plans.get(title="۳ ماهه")
    quarter.title = "۳ ماهه ۲۰٪ تخفیف"
    with pytest.raises(ValidationError) as refused:
        quarter.full_clean()
    assert refused.value.message_dict == {"title": [REFUSAL]}


def test_the_plans_own_form_in_the_panel_refuses_a_discount_in_the_title(client, operator, spotify):
    quarter = spotify.plans.get(title="۳ ماهه")
    url = reverse("admin:catalog_plan_change", args=[quarter.pk])
    data = form_of(client, url, "plan_form")
    data["title"] = ["۳ ماهه ۲۰٪ تخفیف"]

    response = client.post(url, data)

    assert response.status_code == 200, "the panel saved a discount into the plan's title"
    assert REFUSAL in errors_under(response.content.decode(), "title")
    quarter.refresh_from_db()
    assert quarter.title == "۳ ماهه"


def test_names_and_titles_without_a_discount_are_kept(spotify):
    spotify.name, spotify.seo_title = "Spotify Premium", "خرید Spotify Premium"
    spotify.seo_description = "Spotify Premium روی اکانت خودتان."
    spotify.full_clean()
    for plan in spotify.plans.all():
        plan.full_clean()


def _open(page, live_server, product):
    page.goto(live_server.url + reverse("home"))
    page.locator(f'[data-quick-open="{product.pk}"]').first.click()
    dialog = page.get_by_role("dialog", name=product.name)
    expect(dialog).to_be_visible()
    return dialog, dialog.locator(f'[data-quick-product="{product.pk}"]')


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [390, 1440])
def test_the_sheet_shows_a_grey_badge_an_amber_clock_and_each_month_inside_its_row(
    browser, live_server, spotify, width
):
    page = browser.new_page(viewport={"width": width, "height": 844})
    try:
        dialog, sheet = _open(page, live_server, spotify)
        quarter = sheet.locator("li").filter(has_text="۳ ماهه")
        line = quarter.locator(".plan__monthly")
        badge = quarter.locator(".badge--neutral")
        clock = quarter.locator(".deadline")
        expect(line).to_have_text(QUARTER, use_inner_text=True)
        expect(badge).to_have_text("۱۴٪ تخفیف")
        expect(clock).to_have_text("۴ روز و ۵ ساعت مانده")

        def colour(locator):
            return locator.evaluate("e => getComputedStyle(e).color")

        assert colour(badge) == colour(line) == SECONDARY
        assert colour(clock) == TIME_TEXT
        painted = sheet.evaluate("""d => [...d.querySelectorAll('*')].flatMap(n => {
              const s = getComputedStyle(n);
              return [s.color, s.backgroundColor, s.borderTopColor];
            })""")
        assert OLD_ORANGE not in painted
        expect(sheet.locator(".plan__monthly")).to_have_count(2)  # the quarter and the year
        expect(sheet.locator(".plan__promotion")).to_have_count(1)
        assert sheet.evaluate("""d => [...d.querySelectorAll('li')].every(li => {
              const row = li.getBoundingClientRect();
              return [...li.querySelectorAll('.plan__monthly, .plan__promotion')].every(n => {
                const r = n.getBoundingClientRect();
                return r.left >= row.left && r.right <= row.right && r.bottom <= row.bottom;
              });
            })""")
        assert dialog.bounding_box()["height"] <= 844 * 0.85 + 1
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        scrolled = page.evaluate("scrollY")
        details = sheet.locator(".quick-plans__details")
        details.scroll_into_view_if_needed()
        expect(details).to_be_in_viewport()
        assert page.evaluate("scrollY") == scrolled, "the sheet scrolls, never the page"
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_at_the_deadline_the_sheet_drops_the_badge_and_states_the_month_after_it(
    browser, live_server, spotify
):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        page.goto(live_server.url + reverse("home"))  # the first request is the slow one
        spotify.plans.filter(title="۳ ماهه").update(
            promo_ends_at=timezone.now() + timedelta(seconds=5)
        )
        _, sheet = _open(page, live_server, spotify)
        quarter = sheet.locator("li").filter(has_text="۳ ماهه")
        badge = quarter.locator("[data-offer-badge]")
        expect(badge).to_have_count(1)  # rendered while the discount ran
        expect(quarter.locator(".plan__monthly [data-promotion]")).to_have_count(2)
        expect(badge).to_be_hidden(timeout=15000)
        expect(quarter.locator(".deadline")).to_have_text("تخفیف تمام شد")
        expect(quarter.locator(".plan__monthly")).to_have_text(QUARTER_AFTER, use_inner_text=True)
        expect(quarter.locator(".price__num")).to_have_text("۴۹۰,۰۰۰")
    finally:
        page.close()

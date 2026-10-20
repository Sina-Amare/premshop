"""The operator's panel, driven in a real Chromium.

Every bug here passed the test client: it lived in what Unfold's markup, CSS
and scripts do in a browser. A plan's title row was hidden by a class, a drag
wrote its order into an input that was not on the page, and the bulk-action
menu sat under the sidebar where no click could reach it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from playwright.sync_api import Browser, Page

from apps.accounts.models import User
from apps.catalog.models import (
    Category,
    Plan,
    Product,
    ProductQuestion,
    ProductSection,
    ProductSpec,
)
from apps.core.formatting import format_jalali, format_toman
from apps.core.tests.test_accessibility_browser import RUN_AXE, contrast

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

# The width the operator works at, and the phone the owner carries.
DESKTOP, PHONE = 1440, 390
# A control under another element times out; nothing here needs longer.
QUICK = 5_000


@pytest.fixture
def open_panel(browser: Browser, live_server) -> Iterator[Callable[..., Page]]:
    operator = User.objects.create_superuser("operator@example.test")
    client = Client()
    client.force_login(operator)
    cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
    contexts = []

    def open_page(path: str, width: int = DESKTOP, height: int = 900) -> Page:
        context = browser.new_context(viewport={"width": width, "height": height})
        contexts.append(context)
        context.add_cookies(
            [{"name": settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}]
        )
        page = context.new_page()
        page.set_default_timeout(QUICK)
        page.goto(live_server.url + path)
        return page

    yield open_page
    for context in contexts:
        context.close()


@pytest.fixture
def product() -> Product:
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        status=Product.Status.DRAFT,
    )
    Plan.objects.create(
        product=product,
        title="۱ ماهه",
        duration_days=30,
        cost_price=Decimal("900000"),
        sale_price=Decimal("1450000"),
    )
    ProductSpec.objects.create(product=product, title="ریجن", value="آمریکا", sort_order=0)
    ProductSpec.objects.create(product=product, title="تحویل", value="کد", sort_order=1)
    return product


def change_form(product: Product) -> str:
    return f"/admin/catalog/product/{product.pk}/change/"


def with_sections_and_a_question(product: Product) -> None:
    """The prose of a page as the operator writes it: Markdown, Latin names, a
    placeholder still waiting for a decision."""
    ProductSection.objects.create(
        product=product,
        kind=ProductSection.Kind.BENEFITS,
        body="- **متن بلند را خلاصه کنید.** برای دانشجو و پژوهشگر، از جمله در Gemini.",
        sort_order=0,
    )
    ProductSection.objects.create(
        product=product,
        kind=ProductSection.Kind.BEFORE_BUYING,
        body="بازگشت وجه: [تصمیم مالک، تصمیم ۴: چند روز کاری]",
        sort_order=1,
    )
    ProductQuestion.objects.create(
        product=product, question="روی کدام ایمیل فعال می‌شه؟", answer="همان ایمیل اکانت Claude."
    )


def save(page: Page, button: str = "_continue") -> None:
    with page.expect_navigation():
        page.click(f"#submit-row button[name={button}]")


def drag_second_spec_above_the_first(page: Page) -> None:
    # Unfold sorts with SortableJS on native drag and drop, which Playwright
    # drives through drag_to; raw mouse moves never start a native drag.
    handles = page.locator("#specs-group .form-group.original [x-sort\\:handle]")
    handles.nth(1).drag_to(handles.nth(0))


@pytest.mark.parametrize("width", [DESKTOP, PHONE])
def test_a_plan_can_be_added_from_the_product_form(open_panel, product, width):
    """No screen could add a plan: Unfold hides the whole row that holds the
    ordering field, and that row held the title and the duration too."""
    page = open_panel(change_form(product), width)

    page.click("#plans-group a.add-row")
    page.fill("input[name=plans-1-title]", "۳ ماهه")
    page.fill("input[name=plans-1-duration_days]", "90")
    page.fill("input[name=plans-1-cost_price]", "2600000")
    page.fill("input[name=plans-1-sale_price]", "3900000")
    save(page)

    assert list(product.plans.values_list("title", flat=True)) == ["۱ ماهه", "۳ ماهه"]


def test_spec_order_survives_a_save(open_panel, product):
    """Dragging reordered the rows on screen and saved nothing: Unfold's drag
    writes the order into each row's `sort_order` input, and the input was
    not on the page."""
    page = open_panel(change_form(product))

    drag_second_spec_above_the_first(page)
    titles = page.locator("#specs-group .form-group.original input[name$=-title]")
    assert titles.nth(0).input_value() == "تحویل", "the drag itself did not happen"
    save(page)

    assert list(product.specs.values_list("title", flat=True)) == ["تحویل", "ریجن"]


def test_the_bulk_action_menu_can_be_clicked_at_desktop_width(open_panel, product):
    """The only way to publish several products at once sat under the RTL
    sidebar, so a click on it landed on the sidebar."""
    Product.objects.filter(pk=product.pk).update(
        short_description="اشتراک Claude Pro.",
        iran_stance="پشتیبانی‌نشده",
        sources="https://www.anthropic.com/supported-countries",
    )
    page = open_panel("/admin/catalog/product/")

    page.check(f"input.action-select[value='{product.pk}']")
    page.click("#changelist-actions select[name=action]")
    page.select_option("#changelist-actions select[name=action]", "publish")
    with page.expect_navigation():
        page.click("#changelist-actions button[type=submit]")

    product.refresh_from_db()
    assert product.status == Product.Status.ACTIVE


@pytest.mark.parametrize("typed", ["1450000", "145000", "0", "7", "999999999999", "00120"])
def test_the_amount_under_a_price_is_what_the_shop_prints(open_panel, product, typed):
    """One missing zero sells a plan at a tenth of its price, and a bare
    145000 does not look wrong. The amount under the field is printed the way
    the shop prints it, by apps.core.formatting, which this compares against."""
    page = open_panel(change_form(product))
    field = page.locator("input[name=plans-0-sale_price]")

    field.fill(typed)

    preview = page.locator("input[name=plans-0-sale_price] + .money-preview")
    assert preview.inner_text() == format_toman(Decimal(typed))


def test_every_price_field_shows_its_amount_on_load(open_panel, product):
    page = open_panel(change_form(product))

    for name, value in [("cost_price", "900000"), ("sale_price", "1450000")]:
        preview = page.locator(f"input[name=plans-0-{name}] + .money-preview")
        assert preview.inner_text() == format_toman(Decimal(value))
    assert page.locator("input[name=plans-0-promo_price] + .money-preview").is_hidden()


@pytest.mark.parametrize(
    "typed, day",
    [
        ("2026-10-18", date(2026, 10, 18)),
        ("2026-03-20", date(2026, 3, 20)),
        ("2026-03-21", date(2026, 3, 21)),
        ("2025/3/20", date(2025, 3, 20)),
    ],
)
def test_a_discount_date_shows_its_jalali_day(open_panel, product, typed, day):
    """The date picker speaks Gregorian and the operator plans in Mehr and
    Aban. The day is shown under the field as the shop prints dates, by
    apps.core.formatting, across the turn of the Jalali year."""
    page = open_panel(change_form(product))
    field = page.locator("input[name=plans-0-promo_ends_at_0]")

    field.fill(typed)

    preview = page.locator("input[name=plans-0-promo_ends_at_0] ~ .date-preview")
    assert preview.inner_text() == format_jalali(day)


def test_an_impossible_date_shows_no_jalali_day(open_panel, product):
    page = open_panel(change_form(product))

    page.locator("input[name=plans-0-promo_ends_at_0]").fill("2026-02-31")

    assert page.locator("input[name=plans-0-promo_ends_at_0] ~ .date-preview").is_hidden()


def test_leaving_after_a_failed_save_still_warns(open_panel, product):
    """Unfold's warning arms on the first edit after a page loads. A page that
    comes back from a failed save has had none, so everything typed into it
    could be lost by one click on the sidebar."""
    page = open_panel(change_form(product))
    page.fill("input[name=plans-0-promo_price]", "99999999")
    save(page)
    assert page.locator(".errornote").count(), "the save was meant to fail"

    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    page.click("#nav-sidebar a[href$='/admin/catalog/category/']", no_wait_after=True)
    page.wait_for_timeout(500)

    assert dialogs == ["beforeunload"]
    assert page.url.endswith(change_form(product))


def test_the_discount_countdown_is_in_the_time_colour(open_panel, product):
    """The countdown was grey while the code, the commit and the tests all
    said amber (ADR-0016: amber means time and nothing else)."""
    plan = product.plans.get()
    plan.promo_price = Decimal("1190000")
    plan.promo_ends_at = timezone.now() + timedelta(hours=5)
    plan.save()

    page = open_panel("/admin/catalog/product/")
    clock = page.locator("td.field-promo_left .panel-time")

    assert clock.evaluate("e => getComputedStyle(e).color") == "rgb(146, 64, 14)"


@pytest.mark.parametrize("width", [DESKTOP, PHONE])
def test_the_operator_can_put_a_product_on_sale(open_panel, width):
    """A product with two plans and two specs, built, reordered, discounted
    and published from the panel at both widths."""
    category = Category.objects.create(name="سرگرمی", slug="media")
    page = open_panel("/admin/catalog/product/add/", width)

    page.fill("input[name=name]", "Spotify Premium")
    page.select_option("select[name=category]", str(category.pk))
    page.select_option("select[name=delivery_type]", Product.DeliveryType.ON_CUSTOMER_ACCOUNT)
    page.fill("input[name=short_description]", "موسیقی بدون آگهی، روی اکانت خودتان.")
    page.fill("input[name=iran_stance]", "پشتیبانی‌نشده")
    page.fill(
        "textarea[name=sources]",
        "https://support.spotify.com/us/article/full-list-of-territories-where-spotify-is-available/",
    )
    for row, (title, value) in enumerate([("ریجن", "آمریکا"), ("تحویل", "روی اکانت خودتان")]):
        page.fill(f"input[name=specs-{row}-title]", title)
        page.fill(f"input[name=specs-{row}-value]", value)
    page.fill("input[name=plans-0-title]", "۱ ماهه")
    page.fill("input[name=plans-0-duration_days]", "30")
    page.fill("input[name=plans-0-cost_price]", "100000")
    page.fill("input[name=plans-0-sale_price]", "190000")
    page.click("#plans-group a.add-row")
    page.fill("input[name=plans-1-title]", "۱۲ ماهه")
    page.fill("input[name=plans-1-duration_days]", "365")
    page.fill("input[name=plans-1-cost_price]", "1100000")
    page.fill("input[name=plans-1-sale_price]", "1690000")
    page.fill("input[name=plans-1-promo_price]", "1490000")
    save(page)

    drag_second_spec_above_the_first(page)
    save(page)

    page.goto(page.url.split("/admin/")[0] + "/admin/catalog/product/")
    created = Product.objects.get(name="Spotify Premium")
    page.check(f"input.action-select[value='{created.pk}']")
    page.click("#changelist-actions select[name=action]")
    page.select_option("#changelist-actions select[name=action]", "publish")
    with page.expect_navigation():
        page.click("#changelist-actions button[type=submit]")

    created.refresh_from_db()
    assert created.slug == "spotify-premium"
    assert created.status == Product.Status.ACTIVE
    assert list(created.specs.values_list("title", flat=True)) == ["تحویل", "ریجن"]
    plans = list(created.plans.order_by("sort_order", "id"))
    assert [p.title for p in plans] == ["۱ ماهه", "۱۲ ماهه"]
    assert plans[1].promotion_is_active


def test_category_order_survives_a_save(open_panel):
    """Categories are dragged like specs and plans now, not numbered."""
    for order, (name, slug) in enumerate([("هوش مصنوعی", "ai"), ("سرگرمی", "media")]):
        Category.objects.create(name=name, slug=slug, sort_order=order)
    page = open_panel("/admin/catalog/category/")

    handles = page.locator("#result_list [x-sort\\:handle]")
    handles.nth(1).drag_to(handles.nth(0))
    with page.expect_navigation():
        page.click("#submit-row button[name=_save], button[name=_save]")

    assert list(Category.objects.values_list("slug", flat=True)) == ["media", "ai"]


@pytest.mark.parametrize("width", [PHONE, 768, 1023])
def test_the_spec_template_row_stays_hidden_below_desktop_width(open_panel, product, width):
    """Under 1024px each spec cell is a block, and the rule showed the inline's
    template row as well: Tab entered boxes named `__prefix__`, which the
    formset never reads, so whatever was typed there was dropped on save."""
    page = open_panel(change_form(product), width)

    assert page.locator("#id_specs-0-title").is_visible(), "positive control"
    assert page.locator("#id_specs-__prefix__-title").is_hidden()


def test_a_price_range_under_the_name_stays_in_one_piece_on_a_phone(open_panel, product):
    """Each part of «۸۹۰,۰۰۰ تا ۷,۹۰۰,۰۰۰ تومان» was a flex item of its own,
    so «تا» could stand alone on a line, and no two rows broke alike."""
    product.plans.create(
        title="۱۲ ماهه", cost_price=Decimal("9000000"), sale_price=Decimal("14500000")
    )
    page = open_panel("/admin/catalog/product/", PHONE)

    narrow = page.locator(".panel-narrow").first
    assert narrow.is_visible(), "positive control: the line under the name is shown"
    assert narrow.evaluate("e => e.children.length") == 2, "the badge and one range"
    # Each part is unbreakable, and «تا» is part of the second: a range too
    # wide for the cell breaks before «تا» and nowhere else.
    parts = narrow.locator(".panel-amount").all_inner_texts()
    assert len(parts) == 2 and parts[1].startswith("تا "), parts
    assert narrow.locator(".panel-amount").evaluate_all(
        "es => es.every(e => getComputedStyle(e).whiteSpace === 'nowrap')"
    )


def test_after_a_failed_save_the_rows_show_the_order_that_saves(open_panel, product):
    """The page came back in the stored order while each row's `sort_order`
    still held the drag, so saving again published an order nobody saw."""
    page = open_panel(change_form(product))
    drag_second_spec_above_the_first(page)
    page.fill("input[name=plans-0-promo_price]", "99999999")
    save(page)
    assert page.locator(".errornote").count(), "the save was meant to fail"

    rows = page.locator("#specs-group .form-group.original")
    shown = [
        (
            rows.nth(i).locator("input[name$=-title]").input_value(),
            int(rows.nth(i).locator("input[name$=-sort_order]").input_value()),
        )
        for i in range(rows.count())
    ]
    assert [title for title, _ in shown] == ["تحویل", "ریجن"], shown
    assert [order for _, order in shown] == sorted(order for _, order in shown), shown


LATIN_DIGIT = re.compile("[0-9]")


def test_the_panel_numbers_its_rows_and_results_in_persian_digits(open_panel, product):
    """«پلن: #3» under a blank plan and «1 نتیجه (در مجموع 12 تا)» beside a
    search, while every other number in the panel is Persian."""
    Product.objects.create(
        category=product.category,
        name="Spotify Premium",
        slug="spotify-premium",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
    )
    page = open_panel(change_form(product))
    counters = page.locator("#plans-group .form-group:not(.empty-form) .stacked-counter")
    assert counters.all_inner_texts() == ["۲"]
    page.click("#plans-group a.add-row")
    assert counters.all_inner_texts() == ["۲", "۳"]

    page = open_panel("/admin/catalog/product/?q=claude")
    count = page.locator("h1 .text-subtle")
    assert count.is_visible(), "positive control: a search that finds one of two"
    assert not LATIN_DIGIT.search(count.inner_text()), count.inner_text()


def test_the_english_panel_keeps_its_own_digits_and_names(open_panel, product, live_server):
    """The language switch's English side: Latin digits, and English names
    for the controls that are only an icon."""
    page = open_panel("/admin/catalog/product/")
    page.context.add_cookies(
        [{"name": settings.LANGUAGE_COOKIE_NAME, "value": "en", "url": live_server.url}]
    )
    page.reload()
    assert page.evaluate("document.documentElement.lang") == "en", "positive control"

    page.check(f"input.action-select[value='{product.pk}']")

    counter = page.locator(".action-counter").inner_text()
    assert LATIN_DIGIT.search(counter) and not re.search("[۰-۹]", counter), counter
    assert page.get_by_role("button", name="Search").count() >= 1


TEAL = "rgb(15, 118, 110)"


@pytest.mark.parametrize(
    ("path", "start", "key", "control"),
    [
        ("change", "select[name=warranty]", "Tab", "input[name=renewable]"),
        # The button comes before the box in the markup.
        ("/admin/catalog/product/", "#searchbar", "Shift+Tab", "#searchbar-submit"),
    ],
)
def test_a_toggle_and_the_search_button_show_the_keyboards_place(
    open_panel, product, path, start, key, control
):
    """Unfold draws both with `focus:outline-none` and nothing in its place:
    on the product form a keyboard user could not see where they were."""
    page = open_panel(change_form(product) if path == "change" else path)
    page.locator(start).focus()
    page.keyboard.press(key)

    target = page.locator(control)
    assert target.evaluate("e => e === document.activeElement"), "positive control"
    style, width, colour = target.evaluate(
        "e => { const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.outlineColor]; }"
    )
    assert style != "none" and float(width.removesuffix("px")) >= 2, (style, width)
    assert colour == TEAL, colour


# A bar pinned over the lower part of the page that overlaps the focused
# element. A control taller than the room left above the bar (the full
# description's box) only has to start above it.
FOCUS_UNDER_A_BAR = """() => {
    const focused = document.activeElement;
    if (!focused || focused === document.body) return null;
    const bars = [...document.querySelectorAll('body *')].filter(e => {
        const s = getComputedStyle(e);
        return ['fixed', 'sticky'].includes(s.position) && s.display !== 'none'
            && e.getBoundingClientRect().height > 0 && e.getBoundingClientRect().top > 100;
    });
    const a = focused.getBoundingClientRect();
    for (const bar of bars) {
        if (bar.contains(focused)) continue;
        const b = bar.getBoundingClientRect();
        const overlaps = a.bottom > b.top + 1 && a.top < b.bottom;
        if (overlaps && (a.height < 120 || a.top >= b.top)) {
            return focused.name || focused.id || focused.outerHTML.slice(0, 80);
        }
    }
    return null;
}"""


def covered_while_tabbing(page: Page, presses: int) -> list[str]:
    covered = []
    for _ in range(presses):
        page.keyboard.press("Tab")
        if hit := page.evaluate(FOCUS_UNDER_A_BAR):
            covered.append(hit)
    return covered


def test_the_sticky_save_row_never_covers_the_focused_field(open_panel, product):
    """From 1024px the save row sticks to the bottom, and a field scrolled
    into view by Tab landed under it, fully hidden for several stops."""
    page = open_panel(change_form(product), 1280)
    page.locator("input[name=name]").focus()

    assert not covered_while_tabbing(page, 70)


def test_the_bulk_action_bar_never_covers_the_focused_row_on_a_phone(open_panel, product):
    for n in range(12):
        Product.objects.create(
            category=product.category,
            name=f"Product {n}",
            slug=f"product-{n}",
            delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        )
    page = open_panel("/admin/catalog/product/", PHONE)
    page.check(f"input.action-select[value='{product.pk}']")

    assert not covered_while_tabbing(page, 40)


ICON_WORDS = re.compile(
    r"\b(add|search|calendar_today|schedule|expand_more|more_vert|more_horiz|check_small"
    r"|filter_list|history|upload|download|drag_indicator|delete|dock_to_right|storefront"
    r"|arrow_circle_down|arrow_circle_up|cancel|chevron_left|warning|update|Image preview)\b"
)


def spoken(snapshot: str) -> str:
    """What a screen reader says from an ARIA snapshot: its names and text,
    without the role at the head of each line or a link's URL."""
    lines = [line for line in snapshot.splitlines() if "/url:" not in line]
    return "\n".join(re.sub(r"^\s*- [\w-]+", "", line) for line in lines)


def test_no_icon_is_read_aloud_as_its_english_name(open_panel, product):
    """Material Symbols draw each icon from a word, and a screen reader read
    the word: «add», «search», «calendar_today», «check_small» on every box."""
    product.image = "products/claude-pro.svg"
    product.save()
    found = {}
    for path in [change_form(product), "/admin/catalog/product/?o=1", "/admin/"]:
        page = open_panel(path)
        page.wait_for_load_state("load")
        found[path] = sorted(
            {m.group(0) for m in ICON_WORDS.finditer(spoken(page.locator("body").aria_snapshot()))}
        )
        if path == change_form(product):
            # A failed save puts «warning» before each error.
            page.fill("input[name=name]", "")
            save(page)
            assert page.locator(".errorlist").count(), "the save was meant to fail"
            found["a failed save"] = sorted(
                {
                    m.group(0)
                    for m in ICON_WORDS.finditer(spoken(page.locator("body").aria_snapshot()))
                }
            )

    assert not any(found.values()), found
    # The controls that were only an icon now have names of their own.
    page = open_panel("/admin/catalog/product/")
    assert page.get_by_role("button", name="جستجو").count() >= 1
    assert page.get_by_role("link", name="افزودن محصول").count() == 1
    page = open_panel(change_form(product))
    assert page.get_by_role("link", name="انتخاب تاریخ").count() >= 2


def test_the_struck_price_in_the_plan_list_is_readable(open_panel, product):
    """At 0.55 opacity the original price was 2.57:1 on its row, on the page
    where a wrong discount is meant to be spotted."""
    plan = product.plans.get()
    plan.promo_price = Decimal("1190000")
    plan.save()
    page = open_panel("/admin/catalog/plan/")

    was = page.locator(".panel-was")
    opacity, colour = was.evaluate("e => [getComputedStyle(e).opacity, getComputedStyle(e).color]")
    background = was.evaluate("""e => {
        for (let n = e; n; n = n.parentElement) {
            const c = getComputedStyle(n).backgroundColor;
            if (c !== 'rgba(0, 0, 0, 0)') return c;
        }
        return 'rgb(255, 255, 255)';
    }""")
    assert float(opacity) == 1
    assert contrast(colour, background) >= 4.5, (colour, background)


def test_no_panel_page_is_wider_than_a_small_phone(open_panel, product):
    """At 320px the product form was 390px wide: Unfold's file widget would
    not shrink below its path box, and a fieldset never narrows below its
    content, so the whole page scrolled sideways."""
    product.image = "products/claude-pro.svg"
    product.save()
    with_sections_and_a_question(product)
    wide = {}
    for path in [change_form(product), "/admin/catalog/product/", "/admin/catalog/plan/"]:
        page = open_panel(path, 320)
        width = page.evaluate("document.documentElement.scrollWidth")
        if width > 320:
            wide[path] = width
        page.close()

    assert not wide, wide


# --- The pages the storefront's scan does not reach --------------------------

AXE = Path(settings.BASE_DIR) / "node_modules" / "axe-core" / "axe.min.js"


def serious_violations(page: Page) -> list[str]:
    page.add_script_tag(path=str(AXE))
    result = page.evaluate(RUN_AXE)
    assert result["passes"], f"axe checked nothing on {page.url}"
    return [
        f"{v['id']} ({v['help']}) at {' '.join(map(str, node['target']))}"
        for v in result["violations"]
        if v["impact"] in {"serious", "critical"}
        for node in v["nodes"]
    ]


@pytest.mark.parametrize("width", [DESKTOP, PHONE])
def test_no_panel_page_has_a_serious_accessibility_violation(open_panel, product, width):
    """apps/core's scan covers the product list and form; the operator also
    works in these. WCAG 2.2 A and AA, serious and critical, nothing allowed."""
    plan = product.plans.get()
    with_sections_and_a_question(product)
    pages = [
        "/admin/",
        "/admin/catalog/product/add/",
        # A draft: the readiness list, the section guide and both new inlines.
        change_form(product),
        f"/admin/catalog/product/{product.pk}/history/",
        f"/admin/catalog/product/{product.pk}/delete/",
        "/admin/catalog/plan/",
        f"/admin/catalog/plan/{plan.pk}/change/",
        "/admin/catalog/category/",
        f"/admin/catalog/category/{product.category_id}/change/",
        "/admin/accounts/user/",
        "/admin/password_change/",
    ]
    found = {}
    for path in pages:
        page = open_panel(path, width)
        found[path] = serious_violations(page)
        page.close()

    page = open_panel(change_form(product), width)
    page.fill("input[name=plans-0-promo_price]", "99999999")
    save(page)
    found["a failed save"] = serious_violations(page)

    assert not {path: v for path, v in found.items() if v}, found


@pytest.mark.parametrize("width", [DESKTOP, PHONE])
def test_the_sign_in_page_has_no_serious_accessibility_violation(browser, live_server, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        page.goto(f"{live_server.url}/admin/login/")
        page.fill("input[name=username]", "nobody@example.test")
        page.fill("input[name=password]", "wrong")
        with page.expect_navigation():
            page.click("button[type=submit]")

        assert not serious_violations(page)
    finally:
        context.close()


# --- The content model in the panel ------------------------------------------------


def test_section_order_survives_a_save(open_panel, product):
    """The page prints sections in the order the operator drags them."""
    with_sections_and_a_question(product)
    # Tall enough for both rows: a stacked row is taller than a screen, and a
    # synthetic drag does not scroll the page the way a person's does.
    page = open_panel(change_form(product), DESKTOP, 3200)

    handles = page.locator("#sections-group .form-group.original [x-sort\\:handle]")
    handles.nth(1).drag_to(handles.nth(0))
    kinds = page.locator("#sections-group .form-group.original select[name$=-kind]")
    assert kinds.nth(0).input_value() == "before_buying", "the drag itself did not happen"
    save(page)

    assert list(product.sections.values_list("kind", flat=True)) == ["before_buying", "benefits"]


@pytest.mark.parametrize("width", [DESKTOP, PHONE])
def test_a_refused_publish_shows_its_reasons_at_both_widths(open_panel, product, width):
    """The draft lacks its summary and its stance on Iran. Set to «فعال», the
    form comes back with each reason under the field it is about, and all of
    them listed at the top."""
    page = open_panel(change_form(product), width)
    assert page.locator(".panel-readiness li").count() >= 2, "the readiness list names them"

    page.select_option("select[name=status]", Product.Status.ACTIVE)
    save(page)

    for field in ("status", "short_description", "iran_stance"):
        assert page.locator(f"#id_{field}_error").is_visible(), field
    # And together on the first screen, where the save lands.
    reasons = page.locator(".panel-readiness li")
    assert reasons.count() >= 2
    assert reasons.last.evaluate("e => e.getBoundingClientRect().bottom") < 900
    product.refresh_from_db()
    assert product.status == Product.Status.DRAFT


@pytest.mark.parametrize("width", [DESKTOP, PHONE])
def test_sections_are_copied_from_the_list_at_both_widths(open_panel, product, width):
    with_sections_and_a_question(product)
    other = Product.objects.create(
        category=product.category,
        name="Claude Max",
        slug="claude-max",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
    )
    page = open_panel("/admin/catalog/product/", width)

    page.check(f"input.action-select[value='{other.pk}']")
    page.select_option("#changelist-actions select[name=action]", "copy_sections")
    with page.expect_navigation():
        page.click("#changelist-actions button[type=submit]")
    assert not serious_violations(page)
    page.select_option("select[name=source]", str(product.pk))
    with page.expect_navigation():
        page.click("form:has(select[name=source]) button[type=submit]")

    assert list(other.sections.values_list("kind", flat=True)) == ["benefits", "before_buying"]
    assert product.sections.count() == 2, "the original lost its sections"


def test_source_addresses_read_left_to_right(open_panel, product):
    """A URL in a right-to-left box shows its closing slash at the front."""
    page = open_panel(change_form(product))

    box = page.locator("textarea[name=sources]")

    assert box.evaluate("e => getComputedStyle(e).direction") == "ltr"

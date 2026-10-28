"""The buy box's account row opens to say what the product's delivery type means.

The row named the delivery type beside an info icon that did nothing. It is a
<details> now: the summary keeps the row, and the body is one written sentence
about this product's own delivery type, followed on the buyer's own account by
the activation answer the FAQ gives. That answer tells the buyer never to type
a password or a service's code into this site, so the panel refuses a plan
that would ask for one.
"""

from __future__ import annotations

import os
import tempfile
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.template.loader import render_to_string
from playwright.sync_api import expect

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product
from apps.catalog.tests.test_admin import change_url, errors_under, form_of, plan_prefix
from apps.catalog.tests.test_storefront_copy import offences
from apps.core.tests.test_accessibility_browser import STYLED, UNCLIPPED_RING

pytestmark = pytest.mark.django_db

T = Product.DeliveryType
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
SHOTS = (
    Path(os.environ.get("PREMSHOP_SHOTS", Path(tempfile.gettempdir()) / "premshop-shots"))
    / "account-row"
)

ON_ACCOUNT = "اشتراک روی اکانت خودتان فعال می‌شود."
# (the row's label, the sentence its body opens with)
ROWS = {
    T.ON_CUSTOMER_ACCOUNT: ("اکانت", ON_ACCOUNT),
    T.READY_ACCOUNT: (
        "اکانت",
        "یک اکانت آماده تحویل می‌گیرید و با اطلاعات ورود اکانت که به شما می‌دهیم، وارد آن می‌شوید.",
    ),
    T.CODE_LICENSE: (
        "نوع تحویل",
        "یک کد لایسنس تحویل می‌گیرید و آن را خودتان در برنامه وارد می‌کنید.",
    ),
    T.GIFT_CARD: (
        "نوع تحویل",
        "کد گیفت کارت را تحویل می‌گیرید و آن را خودتان در اکانتتان وارد می‌کنید.",
    ),
}
REFUSAL = (
    "رمز اکانت یا کدی را که سرویس می‌فرستد از مشتری نپرسید."
    " صفحه‌ی محصول از مشتری می‌خواهد این‌ها را در سایت وارد نکند."
)
ASKS = "ایمیل اکانت Spotify"
# The colour a custom property resolves to, in the form getComputedStyle reports.
COLOUR_OF = """name => {
    const probe = document.body.appendChild(document.createElement('i'));
    probe.style.color = `var(${name})`;
    const colour = getComputedStyle(probe).color;
    probe.remove();
    return colour;
}"""


def spotify(delivery_type=T.ON_CUSTOMER_ACCOUNT, status=Product.Status.ACTIVE) -> Product:
    category = Category.objects.create(name="موسیقی", slug="music")
    product = Product.objects.create(
        category=category,
        name="Spotify Premium",
        slug="spotify",
        short_description="پخش بدون تبلیغ و دانلود آهنگ‌ها برای گوش دادن بدون اینترنت.",
        delivery_type=delivery_type,
        warranty=Product.Warranty.FULL_PERIOD,
        delivery_hours=6,
        status=status,
    )
    for order, (title, price) in enumerate(
        [("۱ ماهه", 190000), ("۳ ماهه", 490000), ("۱۲ ماهه", 1690000)]
    ):
        Plan.objects.create(
            product=product,
            title=title,
            cost_price=1,
            sale_price=price,
            sort_order=order,
            requires_customer_input=True,
            customer_input_label=ASKS,
        )
    return product


def seeded_spotify(settings) -> Product:
    """Three plans, a discount and per-month prices: the tallest buy box in the seed."""
    settings.DEBUG = True  # the seed refuses to run without it
    call_command("seed_catalog", no_images=True, stdout=StringIO())
    settings.DEBUG = False
    return Product.objects.get(slug="spotify-premium")


class Row(HTMLParser):
    """The row as the server sent it, and every info icon outside it."""

    def __init__(self) -> None:
        super().__init__()
        self.rows, self.lists, self.opened, self.stray_icons = 0, 0, [], 0
        self.inside = self.in_summary = 0
        self.icons: list[str | None] = []
        self.summary: list[str] = []
        self.body: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        row = "buybox__facts" in (attributes.get("class") or "").split()
        self.lists += tag == "dl" and row
        if tag == "details" and (row or self.inside):
            self.inside += 1
            if row:
                self.rows += 1
                self.opened.append("open" in attributes)
        elif tag == "summary" and self.inside:
            self.in_summary += 1
        elif tag == "use" and self.in_summary:
            self.icons.append(attributes.get("href"))
        elif tag == "use" and attributes.get("href") == "#i-info":
            self.stray_icons += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "summary" and self.in_summary:
            self.in_summary -= 1
        elif tag == "details" and self.inside:
            self.inside -= 1

    def handle_data(self, data: str) -> None:
        if self.inside:
            (self.summary if self.in_summary else self.body).append(data)


def row_of(body: str) -> Row:
    row = Row()
    row.feed(body)
    return row


def words(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


@pytest.mark.parametrize("delivery_type", list(ROWS))
def test_the_row_is_a_closed_disclosure_saying_what_its_delivery_type_means(client, delivery_type):
    product = spotify(delivery_type)

    row = row_of(client.get(product.get_absolute_url()).content.decode())

    assert (row.rows, row.lists, row.stray_icons) == (1, 0, 0)
    assert row.opened == [False]
    assert row.icons == ["#i-info", "#i-chevron"]
    label, sentence = ROWS[delivery_type]
    assert words(row.summary) == f"{label} {product.get_delivery_type_display()}"
    if delivery_type == T.ON_CUSTOMER_ACCOUNT:
        sentence += " " + words([render_to_string("catalog/_activation_answer.html")])
    assert words(row.body) == sentence
    assert not offences(sentence, written=True)


# --- What a plan may ask the buyer for --------------------------------------------


def plan_asking(label: str, status=Product.Status.ACTIVE) -> Plan:
    product = spotify(status=status)
    return Plan(
        product=product,
        title="۶ ماهه",
        cost_price=1,
        sale_price=900000,
        requires_customer_input=True,
        customer_input_label=label,
    )


@pytest.mark.parametrize("status", [Product.Status.DRAFT, Product.Status.ACTIVE])
@pytest.mark.parametrize(
    "label",
    [
        "رمز اکانت Spotify",
        "رمزعبور",
        "پسورد اکانت",
        "password",
        "Spotify Password",
        "PASSWORD",
        "کد ورود اکانت",
        "کد تأیید",
        # The spellings a keyboard or a phone gives the same words.
        "کد تایید ایمیل",
        "کد تائید",
        "كد‌ورود",
        "OTP",
        "کد otp",
    ],
)
def test_a_plan_may_not_ask_for_a_password_or_a_login_code(label, status):
    plan = plan_asking(label, status)

    with pytest.raises(ValidationError) as refused:
        plan.full_clean()

    assert refused.value.message_dict == {"customer_input_label": [REFUSAL]}


@pytest.mark.parametrize(
    "label",
    [
        "ایمیل اکانت Spotify",
        "آدرس Gmail",
        "نام کاربری GitHub",
        # A Latin word is matched whole: «otp» is inside this name.
        "ایمیل اکانت SpotPlayer",
        "",
    ],
)
def test_an_honest_label_is_accepted(label):
    plan_asking(label).full_clean()


def test_the_product_form_refuses_it_on_the_plan_row(client):
    client.force_login(User.objects.create_superuser("operator@example.test"))
    product = spotify(status=Product.Status.DRAFT)
    plan = product.plans.first()
    data = form_of(client, change_url(product))
    prefix = plan_prefix(data, plan)

    data[f"{prefix}-customer_input_label"] = ["رمز اکانت Spotify"]
    refused = client.post(change_url(product), data)
    data[f"{prefix}-customer_input_label"] = ["آدرس Gmail"]
    saved = client.post(change_url(product), data)

    assert refused.status_code == 200, "the plan was saved"
    assert REFUSAL in errors_under(refused.content.decode(), f"{prefix}-customer_input_label")
    assert saved.status_code == 302, "positive control: the same form saves an honest label"
    plan.refresh_from_db()
    assert plan.customer_input_label == "آدرس Gmail"


def test_no_seeded_plan_would_be_refused(settings, monkeypatch):
    """The review database is seeded from this catalog."""
    settings.DEBUG = True
    settings.DEMO_CATALOG = True
    monkeypatch.setitem(connection.settings_dict, "NAME", "test_premshop_ui_review")
    call_command("seed_catalog", extended=True, no_images=True, stdout=StringIO())
    asking = Plan.objects.exclude(customer_input_label="").select_related("product")
    assert asking.count() > 3, "positive control: the seed asks buyers for something"

    refused = []
    for plan in asking:
        try:
            plan.full_clean()
        except ValidationError as error:
            refused += error.message_dict.get("customer_input_label", [])

    assert not refused


# --- In a browser -----------------------------------------------------------------


def tab_to(page, locator, presses: int = 80) -> None:
    for _ in range(presses):
        page.keyboard.press("Tab")
        if locator.evaluate("e => e === document.activeElement"):
            return
    raise AssertionError("Tab never reached the row")


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["390", "1440"])
def test_the_keyboard_opens_the_row_and_the_plans_move_down(
    browser, live_server, settings, viewport
):
    SHOTS.mkdir(parents=True, exist_ok=True)
    product = seeded_spotify(settings)
    width = viewport["width"]
    page = browser.new_page(viewport=viewport)
    url = live_server.url + product.get_absolute_url()
    row = page.locator("details.buybox__facts")
    summary = row.locator("summary")
    chevron = summary.locator("svg").last
    note = row.locator("p")
    first_plan = page.locator(".plan").first
    try:
        page.goto(url)
        assert page.evaluate(STYLED)
        assert page.url == url
        expect(page.locator("h1")).to_have_text("Spotify Premium")
        expect(page.locator(".buybox > *").first).to_have_class("buybox__facts")
        if viewport is PHONE:
            chips = page.locator(".pdp__meta").bounding_box()
            assert chips["y"] + chips["height"] <= row.bounding_box()["y"], "not under the chips"
        assert not row.evaluate("e => e.open")
        expect(note).to_be_hidden()
        closed = first_plan.bounding_box()["y"]
        pointing = chevron.evaluate("e => getComputedStyle(e).transform")
        page.screenshot(path=str(SHOTS / f"closed-{width}.png"))

        tab_to(page, summary)
        ring = summary.evaluate(UNCLIPPED_RING)
        assert ring == {"visible": True, "style": "solid", "inside": True}, ring
        assert summary.evaluate("e => getComputedStyle(e).outlineColor") == page.evaluate(
            COLOUR_OF, "--v-accent"
        )
        assert summary.bounding_box()["height"] >= 44

        page.keyboard.press("Enter")
        expect(note).to_be_visible()
        assert row.evaluate("e => e.open")
        assert chevron.evaluate("e => getComputedStyle(e).transform") != pointing
        assert note.evaluate("e => getComputedStyle(e).fontSize") == "13px"
        assert note.evaluate("e => getComputedStyle(e).color") == page.evaluate(
            COLOUR_OF, "--v-text"
        )
        assert first_plan.bounding_box()["y"] >= closed + note.bounding_box()["height"]
        # The plan's own hint stays where it was.
        expect(page.locator("#asks-hint")).to_have_text(f"اطلاعات فعال‌سازی: {ASKS}")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(SHOTS / f"open-{width}.png"))
        if viewport is DESKTOP:
            # Once the box has stuck under the header, the open row leaves the button in view.
            page.evaluate("scrollTo({top: 400, behavior: 'instant'})")
            stuck = page.locator(".pdp__buy").evaluate(
                "e => Math.abs(e.getBoundingClientRect().top - parseFloat(getComputedStyle(e).top)) < 1"
            )
            assert stuck, "the buy box is not stuck"
            cta = page.locator(".buybox__cta")
            expect(cta).to_have_text("افزودن به سبد خرید")
            expect(cta).to_be_in_viewport(ratio=1)
            page.screenshot(path=str(SHOTS / f"open-stuck-{width}.png"))
            page.evaluate("scrollTo({top: 0, behavior: 'instant'})")

        page.keyboard.press("Space")
        expect(note).to_be_hidden()
        page.keyboard.press("Space")
        expect(note).to_be_visible()
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["390", "1440"])
def test_the_row_opens_without_javascript(browser, live_server, viewport):
    product = spotify(T.GIFT_CARD)
    phone = viewport is PHONE
    context = browser.new_context(viewport=viewport, java_script_enabled=False, has_touch=phone)
    page = context.new_page()
    row = page.locator("details.buybox__facts")
    try:
        page.goto(live_server.url + product.get_absolute_url())
        expect(page.locator("h1")).to_have_text("Spotify Premium")
        expect(row.locator("p")).to_be_hidden()
        if phone:
            row.locator("summary").tap()
        else:
            row.locator("summary").click()
        expect(row.locator("p")).to_be_visible()
        expect(row.locator("p")).to_have_text(ROWS[T.GIFT_CARD][1])
    finally:
        context.close()

"""The six-cell code page, driven in a real Chromium.

Everything on that page that matters is JavaScript: cells that advance, paste
that fills all six, a sixth digit that submits, a wrong code that shakes and
clears, a countdown, a resend cooldown. None of it exists in the test client.
So this runs the real page in a real browser against the live server, and it
takes screenshots on the way — those are the artefacts reviewed before the
owner is asked to look at anything visual.

Skipped automatically when Playwright's Chromium is not installed, so CI without
a browser stays green and honest rather than red and ignored.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest
from django.core import mail

from apps.accounts.models import User
from apps.accounts.tests.test_auth import ASCII_TO_PERSIAN, PASSWORD, code_from

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

SHOTS = Path(
    os.environ.get("PREMSHOP_SHOTS", Path(tempfile.gettempdir()) / "premshop-shots" / "e2e")
)

PASTE_JS = """
(text) => {
  const cell = document.querySelector('.otp__cell');
  const dt = new DataTransfer();
  dt.setData('text', text);
  cell.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}
"""


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    # Playwright's sync API drives an event loop under the hood, and Django then
    # refuses ORM calls "from an async context". This is the flag Django's own
    # documentation sets for exactly this case; scoped to the browser fixture.
    previous = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    with playwright.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 — a missing browser is a skip, not a failure
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        yield chromium
        chromium.close()
    if previous is None:
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
    else:
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = previous


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 700, "height": 760}, device_scale_factor=1)
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture
def user():
    return User.objects.create_user("browser@example.test", PASSWORD, is_verified=True)


def request_code(page, live_server, email: str) -> None:
    page.goto(f"{live_server.url}/login/code/")
    page.fill("main input[name=email]", email)
    page.click("main button[type=submit]")  # not the header search button
    page.wait_for_url("**/login/code/verify/")


def test_the_whole_flow_wrong_then_pasted_right(page, live_server, user):
    SHOTS.mkdir(parents=True, exist_ok=True)
    request_code(page, live_server, user.email)

    # Fresh: six cells, the plain field hidden, the clock running, resend cooling down.
    cells = page.locator(".otp__cell")
    assert cells.count() == 6
    assert not page.locator("input[name=code]").is_visible()
    assert "مانده" in page.locator("#code-clock").inner_text()
    assert page.locator("#resend").is_disabled()
    assert cells.first.evaluate("el => document.activeElement === el"), "focus starts in cell 1"
    page.screenshot(path=str(SHOTS / "01-fresh.png"))

    # Typing advances cell by cell; the sixth digit submits by itself.
    page.keyboard.type("00000")
    assert cells.nth(4).input_value() == "0"
    assert cells.nth(5).evaluate("el => document.activeElement === el")
    page.screenshot(path=str(SHOTS / "02-typing.png"))
    page.keyboard.type("0")

    # Wrong: red, an error line, then cleared and refocused — no page change.
    page.wait_for_selector("#code-error:not([hidden])")
    page.screenshot(path=str(SHOTS / "03-wrong.png"))
    error = page.locator("#code-error").inner_text()
    assert error
    page.wait_for_function("() => document.querySelector('.otp__cell').value === ''")
    assert cells.first.evaluate("el => document.activeElement === el")
    assert "/login/code/verify/" in page.url

    # Paste the real code, in Persian digits, exactly as the email shows it.
    persian = code_from(mail.outbox[0]).translate(ASCII_TO_PERSIAN)
    page.evaluate(PASTE_JS, persian)

    # Right: the cells go green one at a time, the way a person reads the code
    # back, and only then does the page leave. Six at once would mean the
    # stagger was lost — which is what the owner asked for on 2026-10-13.
    page.wait_for_selector(".otp__cell.is-ok")
    assert page.locator(".otp__cell.is-ok").count() < 6, "all six turned at once"

    # And the green is actually painted. The class landing is not the same
    # thing: submit() disables the cells before the fetch, and for a while the
    # disabled rule repainted confirmed cells grey at equal specificity — so
    # the stagger ran correctly and invisibly, on the paste path, which is how
    # a code is almost always entered. Read the colour, not the class.
    red, green, blue = page.evaluate(
        r"""() => getComputedStyle(document.querySelector('.otp__cell.is-ok'))
                 .backgroundColor.match(/\d+/g).slice(0, 3).map(Number)"""
    )
    assert green > red and green > blue, f"confirmed cell is not green: rgb({red},{green},{blue})"
    page.screenshot(path=str(SHOTS / "04-ok.png"))
    page.wait_for_function("() => document.querySelectorAll('.otp__cell.is-ok').length === 6")
    page.wait_for_url("**/account/")
    assert user.email in page.content()


def test_the_clock_runs_out_and_resend_brings_a_new_code(page, live_server, user):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.clock.install()
    request_code(page, live_server, user.email)
    # The fake clock freezes CSS transitions mid-way, so a screenshot taken under
    # it shows colours no real user sees (measured: values strictly between the
    # focused and disabled states). Settle them for this test's captures only.
    page.add_style_tag(content="* { transition: none !important; animation: none !important; }")
    assert re.search(r"[۰-۹]+:[۰-۹]{2} مانده", page.locator("#code-clock").inner_text())

    # A minute in: the resend cooldown is over, the code is still live.
    page.clock.fast_forward("01:01")
    page.wait_for_function("() => !document.querySelector('#resend').disabled")
    assert "مانده" in page.locator("#code-clock").inner_text()
    page.screenshot(path=str(SHOTS / "05-resend-ready.png"))

    # Ten minutes in: the code is dead on screen, the cells refuse input.
    page.clock.fast_forward("10:00")
    page.wait_for_function(
        "() => document.querySelector('#code-clock').textContent.includes('منقضی')"
    )
    assert page.locator(".otp__cell").first.is_disabled()
    page.screenshot(path=str(SHOTS / "06-expired.png"))

    # Resend: a new mail, a new clock, cells alive again, and the new code works.
    sent_before = len(mail.outbox)
    page.click("#resend")
    page.wait_for_function("() => document.querySelectorAll('.otp__cell')[0].disabled === false")
    assert len(mail.outbox) == sent_before + 1
    assert "مانده" in page.locator("#code-clock").inner_text()
    assert page.locator("#resend").is_disabled(), "the cooldown restarts after a resend"
    page.screenshot(path=str(SHOTS / "07-after-resend.png"))

    page.evaluate(PASTE_JS, code_from(mail.outbox[-1]))
    page.wait_for_url("**/account/")


def test_the_page_at_phone_width(browser, live_server, user):
    """Chrome's CLI refused a window narrower than ~485px; Playwright does not."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page = context.new_page()
    try:
        request_code(page, live_server, user.email)
        page.screenshot(path=str(SHOTS / "08-phone.png"), full_page=True)
        width = page.evaluate("() => document.documentElement.scrollWidth")
        assert width <= 390, f"horizontal overflow at phone width: {width}px"
        page.goto(f"{live_server.url}/login/")
        page.screenshot(path=str(SHOTS / "09-phone-login.png"), full_page=True)
    finally:
        context.close()


@pytest.fixture
def shelves(db):
    """The shelves row only renders for departments that have something in
    them, so a header test needs a shop behind it."""
    from decimal import Decimal

    from apps.catalog.models import Category, Plan, Product

    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category,
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        warranty=Product.Warranty.FULL_PERIOD,
        delivery_hours=12,
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(
        product=product,
        title="۱ ماهه",
        duration_days=30,
        cost_price=Decimal(850000),
        sale_price=Decimal(1390000),
    )
    return product


def test_the_header_pins_itself_while_the_promise_strip_scrolls_away(browser, live_server, shelves):
    """`position: sticky` fails silently — one `overflow: hidden` on an
    ancestor and it simply stops sticking, with no error anywhere. It also
    only travels inside its own parent, which is why the sticky box here is
    <header> itself rather than a wrapper inside it: wrapped, it would pin
    for the height of the header and then leave with it.

    A shop whose search box disappears the moment you scroll the catalogue
    is a shop that is harder to buy from, so this is worth a browser test.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        assert (
            page.evaluate(
                "() => document.querySelector('.hdr__promise').getBoundingClientRect().top"
            )
            == 0
        )

        page.evaluate("() => window.scrollTo(0, 900)")
        geometry = page.evaluate("""() => ({
                bar: Math.round(document.querySelector('.hdr__main').getBoundingClientRect().top),
                cats: Math.round(document.querySelector('.cats').getBoundingClientRect().top),
                overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            })""")

        assert geometry["bar"] == 0, f"the header did not pin: {geometry}"
        assert geometry["cats"] < 0, "the shelves row should scroll away, not pin"
        assert (
            geometry["overflow"] <= 0
        ), f"the full-bleed bands overflow by {geometry['overflow']}px"
    finally:
        context.close()

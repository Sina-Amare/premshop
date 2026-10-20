"""The six-cell code page, driven in a real Chromium.

Everything on that page that matters is JavaScript: cells that advance, paste
that fills all six, a sixth digit that submits, a wrong code that shakes and
stays red until the next digit, a validity that ends, a resend cooldown. None of it
exists in the test client.
So this runs the real page in a real browser against the live server, and it
takes screenshots on the way — those are the artefacts reviewed before the
owner is asked to look at anything visual.
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

    # Fresh: six cells, the plain field hidden, the validity printed, resend cooling down.
    cells = page.locator(".otp__cell")
    assert cells.count() == 6
    assert not page.locator("input[name=code]").is_visible()
    assert "معتبر تا ساعت" in page.locator("#code-clock").inner_text()
    assert page.locator("#resend").is_disabled()
    assert cells.first.evaluate("el => document.activeElement === el"), "focus starts in cell 1"
    page.screenshot(path=str(SHOTS / "01-fresh.png"))

    # Typing advances cell by cell; the sixth digit submits by itself.
    page.keyboard.type("00000")
    assert cells.nth(4).input_value() == "0"
    assert cells.nth(5).evaluate("el => document.activeElement === el")
    page.screenshot(path=str(SHOTS / "02-typing.png"))
    page.keyboard.type("0")

    # Wrong: the row stays on screen in red with an error line, focus goes back to
    # the first cell, and the page does not change.
    page.wait_for_selector("#code-error:not([hidden])")
    page.screenshot(path=str(SHOTS / "03-wrong.png"))
    error = page.locator("#code-error").inner_text()
    assert error
    assert page.locator(".otp__cell.is-error").count() == 6
    assert cells.nth(5).input_value() == "0", "the slip stays visible"
    page.wait_for_function("() => document.activeElement === document.querySelector('.otp__cell')")
    assert "/login/code/verify/" in page.url

    # The next digit starts the row over.
    page.keyboard.type("1")
    assert page.locator(".otp__cell.is-error").count() == 0
    assert [cells.nth(i).input_value() for i in range(6)] == ["1", "", "", "", "", ""]
    assert page.locator("#code-error").is_hidden()

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
    # a code is almost always entered. Read the colour, not the class, once
    # its background transition has run.
    page.wait_for_timeout(300)
    red, green, blue = page.evaluate(
        r"""() => getComputedStyle(document.querySelector('.otp__cell.is-ok'))
                 .backgroundColor.match(/\d+/g).slice(0, 3).map(Number)"""
    )
    assert green > red and green > blue, f"confirmed cell is not green: rgb({red},{green},{blue})"
    page.screenshot(path=str(SHOTS / "04-ok.png"))
    page.wait_for_function("() => document.querySelectorAll('.otp__cell.is-ok').length === 6")
    page.wait_for_url("**/account/")
    assert user.email in page.content()


def test_the_code_ends_at_a_printed_time_and_resend_brings_a_new_code(page, live_server, user):
    """Nothing on the page counts down (WCAG 2.2.2): the validity is a clock time,
    printed once. A status line speaks a minute before the end and at the end."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.clock.install()
    request_code(page, live_server, user.email)
    # The fake clock freezes CSS transitions mid-way, so a screenshot taken under
    # it shows colours no real user sees (measured: values strictly between the
    # focused and disabled states). Settle them for this test's captures only.
    page.add_style_tag(content="* { transition: none !important; animation: none !important; }")
    printed = page.locator("#code-clock").inner_text()
    assert re.fullmatch("معتبر تا ساعت\u00a0[۰-۹]{2}:[۰-۹]{2}", printed), printed
    assert page.get_attribute("#code-clock", "aria-live") is None
    status = page.locator("#code-status")
    assert status.text_content() == ""

    # A minute in: the resend cooldown is over, the code is still live, and the
    # printed time has not moved.
    page.clock.fast_forward("01:01")
    page.wait_for_function("() => !document.querySelector('#resend').disabled")
    assert page.locator("#code-clock").inner_text() == printed, "the validity ticked"
    assert status.text_content() == "", "nine minutes left is nothing to announce"
    page.screenshot(path=str(SHOTS / "05-resend-ready.png"))

    page.clock.fast_forward("08:00")
    page.wait_for_function(
        "() => document.querySelector('#code-status').textContent.includes('یک دقیقه')"
    )
    assert page.locator("#code-clock").inner_text() == printed

    # Ten minutes in: the code is dead on screen, the cells refuse input, and
    # focus lands on the one thing left to do instead of the page body.
    page.clock.fast_forward("01:00")
    page.wait_for_function(
        "() => document.querySelector('#code-clock').textContent.includes('منقضی')"
    )
    assert page.locator(".otp__cell").first.is_disabled()
    assert page.evaluate("() => document.activeElement.id") == "resend"
    assert "منقضی" in status.text_content()
    assert "badge--time" not in page.get_attribute(
        "#code-clock", "class"
    ), "amber is time still left"
    page.screenshot(path=str(SHOTS / "06-expired.png"))

    # Resend: a new mail, a new validity, cells alive again, and the new code works.
    sent_before = len(mail.outbox)
    page.click("#resend")
    page.wait_for_function("() => document.querySelectorAll('.otp__cell')[0].disabled === false")
    assert len(mail.outbox) == sent_before + 1
    assert "معتبر تا ساعت" in page.locator("#code-clock").inner_text()
    assert "badge--time" in page.get_attribute("#code-clock", "class")
    assert page.locator("#resend").is_disabled(), "the cooldown restarts after a resend"
    assert "کد جدید را فرستادیم" in page.locator("#code-ok").inner_text()
    page.screenshot(path=str(SHOTS / "07-after-resend.png"))

    page.evaluate(PASTE_JS, code_from(mail.outbox[-1]))
    page.wait_for_url("**/account/")


def test_a_page_opened_after_its_code_ran_out_offers_a_new_one_at_once(page, live_server, user):
    """Reloaded after expiry, the page started a 60-second cooldown on a code
    that was already dead, and the focus meant for «ارسال دوباره» found it
    disabled."""
    request_code(page, live_server, user.email)

    def as_if_expired(route):
        response = route.fetch()
        body = re.sub(r'data-expires-in="\d+"', 'data-expires-in="0"', response.text())
        route.fulfill(response=response, body=body)

    page.route("**/login/code/verify/", as_if_expired)
    page.reload()

    assert "منقضی" in page.locator("#code-clock").inner_text()
    assert page.locator("#resend").is_enabled()
    assert page.evaluate("() => document.activeElement.id") == "resend"


def test_a_page_opened_with_under_a_minute_left_offers_resend_when_the_code_runs_out(
    page, live_server, user
):
    """Reloaded with 30 seconds left, the page still started a 60-second
    cooldown: at expiry the cells went dead under the cursor, focus was sent
    to a disabled button and fell to the page, and the button kept counting."""
    request_code(page, live_server, user.email)

    def thirty_seconds_left(route):
        response = route.fetch()
        body = re.sub(r'data-expires-in="\d+"', 'data-expires-in="30"', response.text())
        route.fulfill(response=response, body=body)

    page.route("**/login/code/verify/", thirty_seconds_left)
    page.clock.install()
    page.reload()
    assert page.locator("#resend").is_disabled(), "positive control: the cooldown runs"

    page.clock.fast_forward("00:31")

    assert "منقضی" in page.locator("#code-clock").inner_text()
    assert page.locator("#resend").is_enabled()
    assert page.evaluate("() => document.activeElement.id") == "resend"


def test_without_javascript_the_code_page_still_does_its_whole_job(browser, live_server, user):
    """Resend was a disabled button outside any form, enabled only by script,
    and the page offered no way back from a mistyped address."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        java_script_enabled=False, viewport={"width": 390, "height": 844}, device_scale_factor=1
    )
    page = context.new_page()
    try:
        request_code(page, live_server, user.email)
        assert page.locator("#resend").is_enabled(), "no way to ask for a second code"
        page.click("#resend")
        page.wait_for_url("**/login/code/verify/")
        assert "کد جدید را فرستادیم." in page.locator("main").inner_text()
        assert len(mail.outbox) == 2
        page.screenshot(path=str(SHOTS / "10-nojs-resent.png"), full_page=True)

        page.click("main a:has-text('تغییر ایمیل')")
        page.wait_for_url(re.compile(r"/login/code/\?next="))
        assert page.input_value("main input[name=email]") == user.email

        page.go_back()
        page.fill("main input[name=code]", code_from(mail.outbox[-1]))
        page.click("#code-submit")
        page.wait_for_url("**/account/")
    finally:
        context.close()


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


def test_the_code_cells_stay_inside_the_card_on_a_small_phone(browser, live_server, user):
    """At 320px the six cells ran into the card's padding on both sides and
    ended 6px from its border. The sheet's padding narrows on a small phone, so
    the edge is read from the page rather than assumed."""
    context = browser.new_context(viewport={"width": 320, "height": 700}, device_scale_factor=1)
    page = context.new_page()
    try:
        request_code(page, live_server, user.email)
        sheet = page.locator(".sheet", has=page.locator(".otp"))
        card = sheet.bounding_box()
        inset = sheet.evaluate(
            "e => parseFloat(getComputedStyle(e).paddingLeft)"
            " + parseFloat(getComputedStyle(e).borderLeftWidth)"
        )
        assert inset >= 12, inset
        cells = page.locator(".otp__cell")
        assert cells.count() == 6
        for i in range(6):
            cell = cells.nth(i).bounding_box()
            assert cell["x"] - card["x"] >= inset, (i, cell, card)
            assert card["x"] + card["width"] - (cell["x"] + cell["width"]) >= inset, (i, cell, card)
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


def test_the_header_pins_itself_while_the_support_strip_scrolls_away(browser, live_server, shelves):
    """`position: sticky` fails silently — one `overflow: hidden` on an
    ancestor and it simply stops sticking, with no error anywhere. It also
    only travels inside its own parent, which is why the sticky box here is
    <header> itself rather than a wrapper inside it: wrapped, it would pin
    for the height of the header and then leave with it. The category row
    is inside the header and pins with it.

    A shop whose search box disappears the moment you scroll the catalogue
    is a shop that is harder to buy from, so this is worth a browser test.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/")
        assert (
            page.evaluate("() => document.querySelector('.strip').getBoundingClientRect().top") == 0
        )

        page.evaluate("() => window.scrollTo(0, 900)")
        geometry = page.evaluate("""() => ({
                bar: Math.round(document.querySelector('.site-header').getBoundingClientRect().top),
                cats: Math.round(document.querySelector('.catnav').getBoundingClientRect().top),
                overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            })""")

        assert geometry["bar"] == 0, f"the header did not pin: {geometry}"
        assert geometry["cats"] > 0, f"the category row should pin with the header: {geometry}"
        assert (
            geometry["overflow"] <= 0
        ), f"the full-bleed bands overflow by {geometry['overflow']}px"
    finally:
        context.close()

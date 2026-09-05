"""The six-cell code page, driven in a real Chromium.

Everything on that page that matters is JavaScript: cells that advance, paste
that fills all six, a sixth digit that submits, a wrong code that shakes and
clears, a countdown, a resend cooldown. None of it exists in the test client.
So this runs the real page in a real browser against the live server, and it
takes screenshots on the way — those are the artefacts reviewed before the
owner is asked to look (the rule in CLAUDE.md).

Skipped automatically when Playwright's Chromium is not installed, so CI without
a browser stays green and honest rather than red and ignored.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from django.core import mail

from apps.accounts.models import User
from apps.accounts.tests.test_auth import ASCII_TO_PERSIAN, PASSWORD, code_from

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

SHOTS = Path(
    os.environ.get("PREMSHOP_SHOTS", r"C:\Users\sinaa\AppData\Local\Temp\premshop-shots\e2e")
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

    # Right: all six go green, then the page leaves for the account.
    page.wait_for_selector(".otp__cell.is-ok")
    assert page.locator(".otp__cell.is-ok").count() == 6
    page.screenshot(path=str(SHOTS / "04-ok.png"))
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

"""The six-cell code page, driven in a real Chromium.

Everything on that page that matters is JavaScript: cells that advance, paste
that fills all six, a sixth digit that submits, a wrong code that shakes and
clears, a countdown, a resend cooldown. None of it exists in the test client.
So this runs the real page in a real browser against the live server, and it
takes screenshots on the way — those are the artefacts reviewed before the
owner is asked to look at anything visual.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from django.core import mail

from apps.accounts.models import User
from apps.accounts.tests.test_auth import ASCII_TO_PERSIAN, PASSWORD, code_from

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

PASTE_JS = """
(text) => {
  const cell = document.querySelector('.otp__cell');
  const dt = new DataTransfer();
  dt.setData('text', text);
  cell.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}
"""
# Each timer that turns a cell green is a task of its own, and an observer runs
# after each one, so a row that turned at once is one step from 0 to 6. The
# time of the last step is kept too: load can only make it later.
WATCH_THE_ROW = """() => {
  window.greenCells = [0];
  new MutationObserver(() => {
    const green = document.querySelectorAll('.otp__cell.is-ok').length;
    if (green === window.greenCells.at(-1)) return;
    window.greenCells.push(green);
    window.lastGreen = performance.now();
  }).observe(document.querySelector('.otp'), {subtree: true, attributeFilter: ['class']});
  addEventListener('beforeunload', () => { window.leftAt = window.greenCells.at(-1); });
}"""
# From the answer to the code arriving to the last cell turning green.
ROW_TIME = """() => window.lastGreen - performance.getEntriesByType('resource')
    .filter((entry) => entry.name.endsWith('/login/code/verify/')).at(-1).responseEnd"""


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


def test_resend_stays_disabled_while_its_request_is_pending(page, live_server, user):
    page.clock.install()
    request_code(page, live_server, user.email)
    page.clock.fast_forward("01:01")
    held = []
    page.route("**/login/code/resend/", lambda route: held.append(route))
    page.click("#resend")
    page.wait_for_timeout(50)
    assert len(held) == 1
    try:
        page.clock.fast_forward("00:03")
        assert page.locator("#resend").is_disabled()
        assert page.locator("#resend").inner_text() == "در حال ارسال…"
        page.locator("#resend-form").evaluate(
            "form => form.dispatchEvent(new Event('submit', {cancelable:true}))"
        )
        page.wait_for_timeout(50)
        assert len(held) == 1
    finally:
        held[0].fulfill(json={"ok": False, "error": "کد فرستاده نشد."})


def test_the_whole_flow_wrong_then_pasted_right(page, live_server, user, tmp_path):
    request_code(page, live_server, user.email)

    # Fresh: six cells, the plain field hidden, the clock running, resend cooling down.
    cells = page.locator(".otp__cell")
    assert cells.count() == 6
    assert not page.locator("input[name=code]").is_visible()
    assert "مانده" in page.locator("#code-clock").inner_text()
    assert page.locator("#resend").is_disabled()
    assert cells.first.evaluate("el => document.activeElement === el"), "focus starts in cell 1"
    page.screenshot(path=str(tmp_path / "01-fresh.png"))

    # Typing advances cell by cell; the sixth digit submits by itself.
    page.keyboard.type("00000")
    assert cells.nth(4).input_value() == "0"
    assert cells.nth(5).evaluate("el => document.activeElement === el")
    page.screenshot(path=str(tmp_path / "02-typing.png"))
    page.keyboard.type("0")

    # Wrong: red, an error line, then cleared and refocused — no page change.
    page.wait_for_selector("#code-error:not([hidden])")
    page.screenshot(path=str(tmp_path / "03-wrong.png"))
    error = page.locator("#code-error").inner_text()
    assert error
    page.wait_for_function("() => document.querySelector('.otp__cell').value === ''")
    assert cells.first.evaluate("el => document.activeElement === el")
    assert "/login/code/verify/" in page.url

    # Paste the real code, in Persian digits, exactly as the email shows it.
    # The page records its green cells at every change, and its first way out
    # answers 204, which keeps it in place: what the row did is read from the
    # page itself, not sampled from here inside a 350ms window.
    persian = code_from(mail.outbox[0]).translate(ASCII_TO_PERSIAN)
    page.evaluate(WATCH_THE_ROW)
    left = []

    def stay(route):
        left.append(route.request.url)
        if len(left) == 1:
            route.fulfill(status=204)
        else:
            route.continue_()

    page.route("**/account/", stay)
    with page.expect_response(live_server.url + "/account/") as leaving:
        page.evaluate(PASTE_JS, persian)
    assert leaving.value.status == 204
    assert left == [live_server.url + "/account/"]

    # Right: the cells go green one at a time, the way a person reads the code
    # back, and only then does the page leave. Six at once would mean the
    # stagger was lost — which is what the owner asked for on 2026-10-13.
    steps, left_at = page.evaluate("[window.greenCells, window.leftAt]")
    assert steps == [0, 1, 2, 3, 4, 5, 6], f"the cells did not turn one at a time: {steps}"
    assert left_at == 6, f"the page left with {left_at} green cells"
    # Five 70ms pauses after the answer, which late timers only lengthen; the
    # margin is for timer rounding.
    assert page.evaluate(ROW_TIME) >= 300, "the row turned green without its pauses"

    # And the green is actually painted. The class landing is not the same
    # thing: submit() disables the cells before the fetch, and for a while the
    # disabled rule repainted confirmed cells grey at equal specificity — so
    # the stagger ran correctly and invisibly, on the paste path, which is how
    # a code is almost always entered. Read the colour, not the class, once
    # its fade has ended.
    page.wait_for_function(
        "() => document.querySelector('.otp__cell.is-ok').getAnimations().length === 0"
    )
    red, green, blue = page.evaluate(
        r"""() => getComputedStyle(document.querySelector('.otp__cell.is-ok'))
                 .backgroundColor.match(/\d+/g).slice(0, 3).map(Number)"""
    )
    assert green > red and green > blue, f"confirmed cell is not green: rgb({red},{green},{blue})"
    page.screenshot(path=str(tmp_path / "04-ok.png"))
    page.goto(left[0])
    assert user.email in page.content()


def test_the_clock_runs_out_and_resend_brings_a_new_code(page, live_server, user, tmp_path):
    page.clock.install()
    request_code(page, live_server, user.email)
    # The fake clock freezes CSS transitions mid-way, so a screenshot taken under
    # it shows colours no real user sees (measured: values strictly between the
    # focused and disabled states). Settle them for this test's captures only.
    page.add_style_tag(content="* { transition: none !important; animation: none !important; }")
    assert re.search(r"[۰-۹]+:[۰-۹]{2} مانده", page.locator("#code-clock").inner_text())
    # The clock changes every second, so it is not what a screen reader hears;
    # the status line speaks at one minute left and at expiry, and not before.
    assert page.get_attribute("#code-clock", "aria-hidden") == "true"
    assert page.get_attribute("#code-clock", "aria-live") is None
    status = page.locator("#code-status")
    assert status.text_content() == ""

    # A minute in: the resend cooldown is over, the code is still live.
    page.clock.fast_forward("01:01")
    page.wait_for_function("() => !document.querySelector('#resend').disabled")
    assert "مانده" in page.locator("#code-clock").inner_text()
    assert status.text_content() == "", "nine minutes left is nothing to announce"
    page.screenshot(path=str(tmp_path / "05-resend-ready.png"))

    page.clock.fast_forward("08:00")
    page.wait_for_function(
        "() => document.querySelector('#code-status').textContent.includes('یک دقیقه')"
    )

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
    ), "amber is a running clock"
    page.screenshot(path=str(tmp_path / "06-expired.png"))

    # Resend: a new mail, a new clock, cells alive again, and the new code works.
    sent_before = len(mail.outbox)
    page.click("#resend")
    page.wait_for_function("() => document.querySelectorAll('.otp__cell')[0].disabled === false")
    assert len(mail.outbox) == sent_before + 1
    assert "مانده" in page.locator("#code-clock").inner_text()
    assert "badge--time" in page.get_attribute("#code-clock", "class")
    assert page.locator("#resend").is_disabled(), "the cooldown restarts after a resend"
    page.screenshot(path=str(tmp_path / "07-after-resend.png"))

    page.evaluate(PASTE_JS, code_from(mail.outbox[-1]))
    page.wait_for_url("**/account/")


def test_a_failed_resend_leaves_the_inbox_code_and_its_clock(page, live_server, user, monkeypatch):
    """Owner ruling 2026-11-04: a resend the relay did not take says so, and
    the code already in the inbox still signs in, on the clock it was mailed
    with."""
    from django.core.mail.backends.locmem import EmailBackend

    from apps.accounts.tests.test_auth import disconnect

    page.clock.install()
    request_code(page, live_server, user.email)
    page.clock.fast_forward("01:01")
    page.wait_for_function("() => !document.querySelector('#resend').disabled")
    monkeypatch.setattr(EmailBackend, "send_messages", disconnect)

    page.click("#resend")

    page.wait_for_selector("#code-error:not([hidden])")
    assert "@premshop_support" in page.locator("#code-error").inner_text()
    # A code sent now would have restarted the clock at ten minutes.
    assert page.locator("#code-clock").inner_text().startswith("۸:")
    page.evaluate(PASTE_JS, code_from(mail.outbox[0]))
    page.wait_for_url("**/account/")


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("submit_after", [350, 850], ids=["before-tick", "after-tick"])
def test_a_wrong_code_animation_cannot_reopen_expired_cells(
    page, live_server, user, width, submit_after, tmp_path
):
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install()
    request_code(page, live_server, user.email)
    page.clock.pause_at((datetime.now(UTC) + timedelta(minutes=1)).replace(microsecond=500000))

    def one_second_left(route):
        if route.request.method == "POST":
            route.fulfill(json={"ok": False, "error": "کد ورود درست نیست. دوباره تلاش کنید."})
        else:
            response = route.fetch()
            body = re.sub(r'data-expires-in="\d+"', 'data-expires-in="1"', response.text())
            route.fulfill(response=response, body=body)

    page.route("**/login/code/verify/", one_second_left)
    page.reload()
    page.clock.run_for(submit_after)
    page.evaluate(PASTE_JS, "000000")
    expect(page.locator("#code-error")).to_contain_text("کد ورود درست نیست")
    page.clock.run_for(400)

    expect(page.locator(".otp__cell:enabled")).to_have_count(0)
    expect(page.locator("#code-clock")).to_have_text("کد منقضی شد")
    expect(page.locator("#resend")).to_be_enabled()
    expect(page.locator("#resend")).to_be_focused()
    page.evaluate("document.fonts.ready")
    assert page.url == live_server.url + "/login/code/verify/"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / f"expired-wrong-code-{width}-{submit_after}.png"))

    page.route(
        "**/login/code/resend/",
        lambda route: route.fulfill(json={"ok": True, "expires_in": 600, "cooldown": 60}),
    )
    page.locator("#resend").click()
    expect(page.locator(".otp__cell:enabled")).to_have_count(6)
    expect(page.locator(".otp__cell").first).to_be_focused()


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


def test_without_javascript_the_code_page_still_does_its_whole_job(
    browser, live_server, user, tmp_path
):
    """Resend was a disabled button outside any form, enabled only by script,
    and the page offered no way back from a mistyped address."""
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
        page.screenshot(path=str(tmp_path / "10-nojs-resent.png"), full_page=True)

        page.click("main a:has-text('تغییر ایمیل')")
        page.wait_for_url(re.compile(r"/login/code/\?next="))
        assert page.input_value("main input[name=email]") == user.email

        page.go_back()
        page.fill("main input[name=code]", code_from(mail.outbox[-1]))
        page.click("#code-submit")
        page.wait_for_url("**/account/")
    finally:
        context.close()


def test_the_page_at_phone_width(browser, live_server, user, tmp_path):
    """Chrome's CLI refused a window narrower than ~485px; Playwright does not."""
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page = context.new_page()
    try:
        request_code(page, live_server, user.email)
        page.screenshot(path=str(tmp_path / "08-phone.png"), full_page=True)
        width = page.evaluate("() => document.documentElement.scrollWidth")
        assert width <= 390, f"horizontal overflow at phone width: {width}px"
        page.goto(f"{live_server.url}/login/")
        page.screenshot(path=str(tmp_path / "09-phone-login.png"), full_page=True)
    finally:
        context.close()


def test_the_code_cells_stay_inside_the_card_on_a_small_phone(browser, live_server, user):
    """At 320px the six cells ran into the card's padding on both sides and
    ended 6px from its border."""
    context = browser.new_context(viewport={"width": 320, "height": 700}, device_scale_factor=1)
    page = context.new_page()
    try:
        request_code(page, live_server, user.email)
        card = page.locator(".card", has=page.locator(".otp")).bounding_box()
        cells = page.locator(".otp__cell")
        assert cells.count() == 6
        for i in range(6):
            cell = cells.nth(i).bounding_box()
            assert cell["x"] - card["x"] >= 16, (i, cell, card)
            assert card["x"] + card["width"] - (cell["x"] + cell["width"]) >= 16, (i, cell, card)
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


def test_the_header_and_category_navigation_remain_available_while_scrolling(
    browser, live_server, shelves
):
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
            page.evaluate("() => document.querySelector('.v-header').getBoundingClientRect().top")
            == 0
        )

        page.evaluate("() => window.scrollTo(0, 900)")
        geometry = page.evaluate("""() => ({
                bar: Math.round(document.querySelector('.v-header__main').getBoundingClientRect().top),
                cats: Math.round(document.querySelector('.v-navline').getBoundingClientRect().top),
                overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            })""")

        assert geometry["bar"] == 0, f"the header did not pin: {geometry}"
        assert geometry["cats"] > 0, "category navigation should remain below the pinned header"
        assert (
            geometry["overflow"] <= 0
        ), f"the full-bleed bands overflow by {geometry['overflow']}px"
    finally:
        context.close()

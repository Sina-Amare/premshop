"""Auth is a money path: whoever holds a session can see delivered credentials.

Every test here names a way in that must stay shut, or a way in that must stay
open — a customer locked out by an over-tight limit is a refund, not a save.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import pytest
import sentry_sdk
import time_machine
from django.contrib.auth.hashers import MD5PasswordHasher, make_password
from django.core.cache import cache
from django.urls import reverse
from sentry_sdk.transport import Transport

from apps.accounts import otp, services, views
from apps.accounts.models import User
from apps.core import ratelimit, sms, support
from apps.core.observability import sentry_options

pytestmark = pytest.mark.django_db

PASSWORD = "CorrectHorse!2026"  # noqa: S105 — a fixture value, not a credential
START = 1_790_000_000  # a fixed instant, so time-travel tests never depend on the clock
PHONE = "09121112233"
PERSIAN_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
ASCII_TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


@pytest.fixture
def user():
    return User.objects.create_user(
        "customer@example.test", PASSWORD, phone=PHONE, is_verified=True
    )


def code_from(sent: tuple[str, str]) -> str:
    """Pull the six digits out of a sent SMS, (number, text) in the fake outbox."""
    found = re.search(r"\b\d{6}\b", sent[1])
    assert found, f"no six-digit code in the message: {sent[1][:200]}"
    return found.group(0)


def leaked(code: str, text: str) -> bool:
    """A code leaks only as a number of its own: inside a longer run of digits, a package
    version or a timestamp's fraction in an error report, the same six digits are chance."""
    return re.search(rf"(?<![\d.]){code}(?!\d)", text) is not None


def test_a_code_counts_as_leaked_only_as_a_number_of_its_own():
    assert leaked("202608", "login code 202608 sent")
    assert leaked("123456", '{"message": "code=123456"}')
    assert not leaked("202608", '"types-requests": "6.0.12.20260815"')
    assert not leaked("883181", "2026-11-06T20:47:53.883181Z")


# --- The model -------------------------------------------------------------


def test_the_manager_lowercases_on_the_way_in():
    created = User.objects.create_user("MiXeD@Example.COM")
    assert created.email == "mixed@example.com"


def test_a_user_created_without_a_password_cannot_be_logged_into_by_password():
    """An OTP-only account must not be reachable with an empty password."""
    created = User.objects.create_user("otponly@example.test")

    assert not created.has_usable_password()
    assert not created.check_password("")


# --- The panel's password ----------------------------------------------------


def test_an_inactive_account_cannot_sign_in_by_code(client, user):
    user.is_active = False
    user.save(update_fields=["is_active"])

    client.post(reverse("login-code"), {"phone": user.phone})
    client.post(reverse("login-code-verify"), {"code": code_from(sms.outbox[-1])})

    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("case", ["unknown", "wrong_password", "no_password", "inactive", "right"])
def test_every_password_attempt_costs_exactly_one_hash(client, user, monkeypatch, case):
    """If a miss on a real account costs different work from a miss on a stranger,
    the response time tells anyone which addresses have an account — and for this
    shop that list is itself sensitive. So every attempt at the panel's sign-in,
    whatever its outcome, runs exactly one password hash.

    Counted, not timed: a timing assertion would be flaky, while the number of hash
    computations is exact and is what the time difference is made of. It was two for
    a real account with a wrong password, and two for an account with no password
    (Django already fakes one hash for those; the view added a second)."""
    monkeypatch.setattr(services, "_DUMMY_HASH", make_password("nobody-knows-this"))
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    username, password = user.phone, "not-the-password"
    if case == "unknown":
        username = "09129999999"
    elif case == "no_password":
        username = User.objects.create_user(phone="09128888888").phone
    elif case == "inactive":
        user.is_active = False
        user.save(update_fields=["is_active"])
    elif case == "right":
        password = PASSWORD

    hashes = []
    real_encode = MD5PasswordHasher.encode

    def counting_encode(self, *args, **kwargs):
        hashes.append(1)
        return real_encode(self, *args, **kwargs)

    monkeypatch.setattr(MD5PasswordHasher, "encode", counting_encode)

    client.post(reverse("admin:login"), {"username": username, "password": password})

    assert len(hashes) == 1, f"{case}: {len(hashes)} hashes"


# --- OTP login -------------------------------------------------------------


def test_otp_login_creates_session_and_sets_verified(client):
    """The whole flow as a customer walks it: ask for a code, receive an SMS, enter it."""
    unverified = User.objects.create_user(phone="09125550002")
    assert not unverified.is_verified

    client.post(reverse("login-code"), {"phone": unverified.phone})
    assert len(sms.outbox) == 1

    response = client.post(reverse("login-code-verify"), {"code": code_from(sms.outbox[0])})

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(unverified.pk)
    unverified.refresh_from_db()
    assert unverified.is_verified, "receiving the code IS the verification"


def test_a_code_for_an_unknown_number_creates_the_account(client):
    """One flow does sign-in and registration, so there is no signup form and no
    user-enumeration oracle to probe."""
    assert not User.objects.filter(phone="09125550003").exists()

    client.post(reverse("login-code"), {"phone": "09125550003"})
    client.post(reverse("login-code-verify"), {"code": code_from(sms.outbox[0])})

    created = User.objects.get(phone="09125550003")
    assert created.is_verified
    assert not created.has_usable_password()


def test_a_code_is_single_use(client, user):
    client.post(reverse("login-code"), {"phone": user.phone})
    code = code_from(sms.outbox[0])
    client.post(reverse("login-code-verify"), {"code": code})
    client.post(reverse("logout"))

    client.post(reverse("login-code"), {"phone": user.phone})
    client.post(reverse("login-code-verify"), {"code": code})

    assert "_auth_user_id" not in client.session


def test_otp_expired_rejected(client, user):
    client.post(reverse("login-code"), {"phone": user.phone})
    code = code_from(sms.outbox[0])

    cache.clear()  # what a TTL expiry looks like from the code's point of view

    client.post(reverse("login-code-verify"), {"code": code})

    assert "_auth_user_id" not in client.session


def test_persian_digits_in_the_code_are_accepted(client, user):
    """A customer who types the code in Persian digits must get in — otherwise the
    shop tells them, correctly and uselessly, that the code is wrong."""
    client.post(reverse("login-code"), {"phone": user.phone})
    persian = code_from(sms.outbox[0]).translate(ASCII_TO_PERSIAN)

    client.post(reverse("login-code-verify"), {"code": persian})

    assert client.session["_auth_user_id"] == str(user.pk)


def test_the_code_is_never_stored_in_the_clear(client, user):
    client.post(reverse("login-code"), {"phone": user.phone})
    code = code_from(sms.outbox[0])

    # The number's key holds only the issuance; the record it points to holds the code.
    issuance = cache.get(f"otp:{user.phone}")
    stored = cache.get(f"otp-code:{issuance}:{user.phone}")

    assert stored is not None and stored["digest"]
    assert code not in str(stored)


# --- Rate limiting ---------------------------------------------------------


def test_a_refused_request_sends_nothing_and_says_why(client, user):
    """Every SMS is paid for: a refused request must not still send one."""
    for _ in range(ratelimit.OTP_REQUESTS_PER_PHONE_COOLDOWN.times):
        client.post(reverse("login-code"), {"phone": user.phone})
    sent_before = len(sms.outbox)

    response = client.post(reverse("login-code"), {"phone": user.phone})

    assert len(sms.outbox) == sent_before, "a refused request must not still send an SMS"
    assert services.CODE_COOLDOWN in response.content.decode()


@pytest.mark.parametrize(
    ("url", "limit", "message"),
    [
        ("login-code", ratelimit.OTP_REQUESTS_PER_IP, services.TOO_MANY_CODES_FROM_NETWORK),
    ],
)
def test_a_spent_network_budget_says_so_without_blaming_the_visitor(client, url, limit, message):
    """A mobile carrier puts many customers behind one address. The message for
    the budget per IP said «you have asked several times in a row» to whoever
    came next, and gave no number for the wait."""
    for n in range(limit.times):
        client.post(reverse(url), {"phone": f"0912000{n:04d}"})

    body = client.post(reverse(url), {"phone": "09129999999"})

    assert message in body.content.decode()


def test_the_code_is_burned_after_too_many_wrong_guesses(client, user):
    client.post(reverse("login-code"), {"phone": user.phone})
    code = code_from(sms.outbox[0])

    for _ in range(otp.MAX_ATTEMPTS):
        client.post(reverse("login-code-verify"), {"code": "000000"})

    client.post(reverse("login-code-verify"), {"code": code})

    assert "_auth_user_id" not in client.session, "the real code must die with the budget"


def test_a_guessed_out_code_stays_dead_when_its_counter_is_lost(user):
    """The count lives in a key of its own. Lost alone, to eviction or to a
    restore taken between the two writes, it gave a guessed-out code a fresh
    budget of guesses."""
    issued = otp.issue(user.phone)
    issuance = cache.get(otp._key(user.phone))
    wrong = f"{(int(issued.code) + 1) % 10**otp.CODE_LENGTH:0{otp.CODE_LENGTH}d}"
    for _ in range(otp.MAX_ATTEMPTS):
        otp.verify(user.phone, wrong)

    cache.delete(otp._attempts_key(user.phone, issuance))

    assert not otp.verify(user.phone, issued.code)


class WindowExpiredInsideIncr:
    """The real cache, except that a window's key expires between the EXISTS
    and the INCR inside Django's RedisCache.incr(): Redis then creates the key
    again at 1 and gives it no expiry."""

    def __init__(self) -> None:
        self.touched: list[tuple[str, int]] = []

    def add(self, key: str, value: object, timeout: int) -> bool:
        return False

    def incr(self, key: str) -> int:
        cache.set(key, 1, timeout=None)
        return 1

    def touch(self, key: str, timeout: int) -> bool:
        self.touched.append((key, timeout))
        return cache.touch(key, timeout)

    def __getattr__(self, name: str) -> object:
        return getattr(cache, name)


def test_a_rate_limit_window_recreated_by_incr_still_expires(monkeypatch):
    """A budget with no expiry never comes back: behind one proxy address,
    every sign-in on the shop and the panel would be refused for good."""
    window = WindowExpiredInsideIncr()
    monkeypatch.setattr(ratelimit, "cache", window)

    assert ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, "203.0.113.9")

    assert window.touched == [("rl:login:ip:203.0.113.9", ratelimit.LOGIN_ATTEMPTS_PER_IP.seconds)]


def test_a_code_counter_recreated_by_incr_still_expires_and_fails_closed(monkeypatch, user):
    """The counter expires with the code it counts, so one that came back from
    INCR means the code's life is over; left without an expiry it would also
    count against every later code until the next one was issued."""
    issued = otp.issue(user.phone)
    issuance = cache.get(otp._key(user.phone))
    window = WindowExpiredInsideIncr()
    monkeypatch.setattr(otp, "cache", window)

    assert not otp.verify(user.phone, issued.code)

    [(key, timeout)] = window.touched
    assert key == otp._attempts_key(user.phone, issuance)
    assert 0 < timeout <= otp.CODE_TTL_SECONDS


def test_a_wrong_guess_does_not_extend_the_codes_life(user):
    """Otherwise an attacker buys time simply by guessing at it. A guess a minute
    before the deadline must not keep the code alive past that deadline.

    The test this replaces only checked that the code still worked straight after
    a wrong guess, so it could not fail for the bug it was named after: on Redis,
    every wrong guess reset the code's life to five minutes."""
    with time_machine.travel(START, tick=False) as clock:
        issued = otp.issue(user.phone)
        clock.shift(otp.CODE_TTL_SECONDS - 60)
        otp.verify(user.phone, "000000")
        clock.shift(61)

        assert not otp.verify(user.phone, issued.code), "the code outlived its deadline"


def test_a_wrong_guess_does_not_shorten_the_codes_life(user):
    """The page counts down ten minutes. A typo in the first second must not
    quietly make it five, while the countdown still shows time left."""
    with time_machine.travel(START, tick=False) as clock:
        issued = otp.issue(user.phone)
        otp.verify(user.phone, "000000")
        clock.shift(otp.CODE_TTL_SECONDS - 60)

        assert otp.verify(user.phone, issued.code), "a typo cut the code's life short"


def test_a_successful_login_clears_the_attempt_budget(client, user):
    client.post(reverse("login-code"), {"phone": user.phone})
    code = code_from(sms.outbox[-1])
    wrong = f"{(int(code) + 1) % 10**otp.CODE_LENGTH:0{otp.CODE_LENGTH}d}"
    client.post(reverse("login-code-verify"), {"code": wrong})
    client.post(reverse("login-code-verify"), {"code": code})

    assert ratelimit.remaining(ratelimit.LOGIN_ATTEMPTS_PER_PHONE, user.phone) == (
        ratelimit.LOGIN_ATTEMPTS_PER_PHONE.times
    )


def test_two_spellings_of_a_number_share_one_budget(client, user):
    client.post(reverse("login-code"), {"phone": user.phone})
    sent_before = len(sms.outbox)

    client.post(reverse("login-code"), {"phone": "+98 912 111 2233"})

    assert len(sms.outbox) == sent_before, "another spelling must not buy a new budget"


# --- Error reports -----------------------------------------------------------


@pytest.fixture
def error_reports():
    """Error tracking configured exactly as production configures it
    (`sentry_options`), with a transport that keeps each report in memory instead
    of sending it. Whatever lands here is what GlitchTip would receive."""
    reports: list[dict] = []

    class KeepInMemory(Transport):
        def capture_envelope(self, envelope):
            event = envelope.get_event()
            if event is not None:
                reports.append(event)

    sentry_sdk.init(
        **sentry_options(dsn="https://key@glitchtip.invalid/1", environment="test"),
        transport=KeepInMemory,
    )
    yield reports
    sentry_sdk.init()  # no DSN: reporting is off again for every other test


# --- A code the relay did not take ------------------------------------------


def fail_the_sms(monkeypatch, sent: list[str]) -> None:
    """Make the next code SMS fail as SMS.ir can. The error carries the number and
    the code, so a log or a report that kept its text would be caught holding both."""

    def refuse(mobile: str, code: str) -> None:
        sent.append(code)
        error = sms.SmsNotSent(400, 0)
        error.args = (f"SMS.ir refused {mobile}: {code}",)
        raise error

    monkeypatch.setattr(sms, "send_code", refuse)


# Owner ruling 2026-11-04 on the wording; the support handle follows it.
CODE_NOT_SENT = (
    "کد ورود فرستاده نشد. چند دقیقه دیگر دوباره امتحان کنید یا در تلگرام به پشتیبانی پیام بدهید:"
)


def daily_left(phone: str) -> tuple[int, int]:
    return (
        ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, phone),
        ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_IP_DAILY, "127.0.0.1"),
    )


@pytest.mark.parametrize("path", ["request", "request-json", "resend", "resend-json"])
def test_a_code_sms_ir_did_not_take_keeps_the_visitor_on_the_page(
    client, user, monkeypatch, caplog, error_reports, path
):
    """A refused send is answered on the same page with a Persian message, no daily
    unit spent, and one log line that names the failure without the number or the
    code (ADR-0008)."""
    json_answer = {"HTTP_ACCEPT": "application/json"} if path.endswith("json") else {}
    if path.startswith("resend"):
        client.post(reverse("login-code"), {"phone": user.phone})
        # Two minutes have passed, as far as the number's cooldown is concerned.
        ratelimit.reset(ratelimit.OTP_REQUESTS_PER_PHONE_COOLDOWN, user.phone)
    before = daily_left(user.phone)
    sent: list[str] = []
    fail_the_sms(monkeypatch, sent)

    if path.startswith("request"):
        response = client.post(reverse("login-code"), {"phone": user.phone}, **json_answer)
    else:
        response = client.post(reverse("login-code-resend"), **json_answer)

    handle = support.details()["telegram"]
    on_a_page = f'{CODE_NOT_SENT} <bdi class="latin">{handle}</bdi>'
    if json_answer:
        # A script sets this as text, so the handle is isolated by the marks
        # <bdi> stands for; without them its @ is drawn at the wrong end.
        assert response.status_code == 503
        assert response.json() == {"ok": False, "error": f"{CODE_NOT_SENT} \u2066{handle}\u2069"}
    elif path == "resend":
        assert response.headers["Location"] == reverse("login-code-verify")
        assert on_a_page in client.get(response.headers["Location"]).content.decode()
    else:
        assert response.status_code == 503
        assert "accounts/login_code_request.html" in [used.name for used in response.templates]
        assert on_a_page in response.content.decode()
    if path.startswith("request"):
        assert views.PENDING_PHONE_KEY not in client.session, "the code page is not reachable"
    assert daily_left(user.phone) == before, "a code nobody received spent a daily unit"
    everything = json.dumps(error_reports) + caplog.text
    assert len([r for r in error_reports if r.get("logger") == "apps.accounts.services"]) == 1
    assert sent and not leaked(sent[-1], everything), "the code reached a log or a report"
    assert user.phone not in everything, "the number reached a log or a report"


@pytest.mark.parametrize("plain", [False, True], ids=["script", "form"])
def test_a_failed_resend_keeps_the_code_already_on_the_phone(client, user, monkeypatch, plain):
    """Until a send is accepted, the code already on the phone works, on its own
    clock (owner ruling 2026-11-04)."""
    client.post(reverse("login-code"), {"phone": user.phone})
    on_the_phone = code_from(sms.outbox[-1])
    expires_at = client.session[views.PENDING_EXPIRES_KEY]
    ratelimit.reset(ratelimit.OTP_REQUESTS_PER_PHONE_COOLDOWN, user.phone)
    unsent: list[str] = []
    fail_the_sms(monkeypatch, unsent)

    json_answer = {} if plain else {"HTTP_ACCEPT": "application/json"}
    response = client.post(reverse("login-code-resend"), **json_answer)

    assert response.status_code == (302 if plain else 503)
    assert unsent, "the resend never made a code"
    assert client.session[views.PENDING_EXPIRES_KEY] == expires_at, "the countdown restarted"
    outcomes = [
        client.post(reverse("login-code-verify"), {"code": code}).status_code
        for code in (unsent[-1], on_the_phone)
    ]
    # The unsent code, which nobody received, is refused; the code on the phone signs in.
    assert outcomes == [200, 302], outcomes
    assert client.session["_auth_user_id"] == str(user.pk)


@pytest.mark.parametrize("wrong_after", [0, 1])
def test_a_failed_send_neither_refills_nor_spends_the_guesses_left(user, monkeypatch, wrong_after):
    """A failed send that gave the inbox code a fresh count would make each
    failed send five more guesses at six digits."""
    inbox = services._send_code(user.phone).code
    wrong = f"{(int(inbox) + 1) % 10**otp.CODE_LENGTH:0{otp.CODE_LENGTH}d}"
    for _ in range(otp.MAX_ATTEMPTS - 1):
        assert not otp.verify(user.phone, wrong)
    with monkeypatch.context() as failing:
        failing.setattr(sms, "send_code", disconnect)
        with pytest.raises(services.CodeNotSent):
            services._send_code(user.phone)

    for _ in range(wrong_after):
        assert not otp.verify(user.phone, wrong)

    # One guess was left before the failed send, and is still left: the right
    # code spends it, and a wrong one spends it and the code with it.
    assert otp.verify(user.phone, inbox) is (wrong_after == 0)


@pytest.mark.parametrize("late", [False, True], ids=["before-deadline", "after-deadline"])
def test_a_failed_send_does_not_move_the_inbox_codes_deadline(user, monkeypatch, late):
    """Nor may it buy the code time: the deadline is the one it was sent with."""
    with time_machine.travel(START, tick=False) as clock:
        inbox = services._send_code(user.phone).code
        clock.shift(otp.CODE_TTL_SECONDS - 60)
        with monkeypatch.context() as failing:
            failing.setattr(sms, "send_code", disconnect)
            with pytest.raises(services.CodeNotSent):
                services._send_code(user.phone)
        clock.shift(61 if late else 59)

        assert otp.verify(user.phone, inbox) is not late


def disconnect(*args, **kwargs):
    raise sms.SmsNotSent(None)


# --- Sessions and pages ----------------------------------------------------


def test_login_rotates_the_session_key(client, user):
    """Session fixation: an attacker who plants a known session id in the browser
    before login must not be able to reuse it afterwards."""
    client.session.create()
    before = client.session.session_key

    client.post(reverse("login-code"), {"phone": user.phone})
    client.post(reverse("login-code-verify"), {"code": code_from(sms.outbox[-1])})

    assert client.session.session_key != before


def test_logout_refuses_get(client, user):
    """A GET logout can be fired by any <img> on any site — cross-site request
    forgery that logs customers out for entertainment."""
    client.force_login(user)

    response = client.get(reverse("logout"))

    assert response.status_code == 405
    assert "_auth_user_id" in client.session


def test_the_account_page_requires_a_session(client):
    response = client.get(reverse("account"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_the_public_sign_in_page_renders(client):
    """A smoke test, because a missing {% load %} is a 500 no unit test sees and
    the first customer to reach the page finds instead. This caught exactly that
    on the account page."""
    response = client.get(reverse("login-code"))

    assert response.status_code == 200
    assert (
        "PremShop" in response.content.decode()
    ), "the wordmark is the brand ruling: Latin, everywhere"


@pytest.mark.parametrize("name", ["login-code", "login-code-verify", "account"])
def test_sign_in_and_account_pages_ask_not_to_be_indexed(client, user, name):
    """Nothing on them is worth a search result, and a results page that lands
    a stranger on a code-entry form is a phishing kit's favourite shape."""
    if name == "account":
        client.force_login(user)
    elif name == "login-code-verify":
        client.post(reverse("login-code"), {"phone": user.phone})

    response = client.get(reverse(name))

    assert response.status_code == 200
    assert '<meta name="robots" content="noindex">' in response.content.decode()


def test_the_account_page_renders_for_a_signed_in_user(client, user):
    client.force_login(user)

    response = client.get(reverse("account"))

    assert response.status_code == 200
    assert user.email in response.content.decode()


# --- Where you land after signing in --------------------------------------


def test_code_login_returns_you_to_the_page_you_asked_for(client):
    """The code flow spans three requests, so the destination rides the
    session rather than the URL."""
    client.post(
        reverse("login-code") + "?next=/account/orders/",
        {"phone": "09125550004", "next": "/account/orders/"},
    )
    code = code_from(sms.outbox[-1])

    response = client.post(reverse("login-code-verify"), {"code": code})

    assert response.status_code == 302
    assert response.headers["Location"] == "/account/orders/"


def test_the_json_path_reports_the_same_destination(client):
    client.post(
        reverse("login-code") + "?next=/account/orders/",
        {"phone": "09125550005", "next": "/account/orders/"},
    )
    code = code_from(sms.outbox[-1])

    response = client.post(
        reverse("login-code-verify"), {"code": code}, headers={"accept": "application/json"}
    )

    assert json.loads(response.content)["next"] == "/account/orders/"


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/phish",
        "//evil.example/phish",
        "http://evil.example",
        "https://premshop.ir.evil.example/",
        "\\evil.example/phish",
        "https:evil.example",
    ],
)
def test_next_cannot_send_anyone_off_this_site(client, hostile):
    """Without the host check `?next=` turns a login page into an open
    redirect, which is worth more to a phisher on the shop's own domain than
    on anyone else's.

    The assertion resolves the Location against this origin with `urljoin`
    rather than asking Django's own validator again — a check that reuses the
    value under test cannot see that the value is wrong. A backslash form is
    allowed through deliberately: browsers read it as a path separator, so it
    lands on a path of *this* site, which is a 404 and not a phishing hop.
    """
    response = client.post(reverse("login-code"), {"next": hostile, "phone": "09125550006"})
    assert response.status_code == 302

    # The code flow stores it; follow through to where it actually sends you.
    code = code_from(sms.outbox[-1])
    landing = client.post(reverse("login-code-verify"), {"code": code}).headers["Location"]

    assert urlparse(urljoin("http://testserver/", landing)).netloc == "testserver", landing


# A Persian query and a second parameter: an unencoded `&` would split the
# destination, and an unencoded Persian word would arrive garbled.
SEARCH_PAGE = "/search/?" + urlencode({"q": "نتفلیکس", "page": "2"})


def header_sign_in_link(client, path: str) -> str:
    body = client.get(path).content.decode()
    found = re.search(r'class="v-account" href="([^"]*)"', body)
    assert found, "no account link in the header"
    return html.unescape(found.group(1))


def hidden_next(body: str) -> str:
    found = re.search(r'name="next" value="([^"]*)"', body)
    assert found, "no next field on the sign-in form"
    return html.unescape(found.group(1))


def test_the_header_sign_in_link_carries_the_page_it_was_on(client):
    link = urlparse(header_sign_in_link(client, SEARCH_PAGE))

    assert link.path == reverse("login-code")
    assert parse_qs(link.query) == {"next": [SEARCH_PAGE]}


def test_signing_in_from_the_header_link_returns_to_that_page(client, user):
    body = client.get(header_sign_in_link(client, SEARCH_PAGE)).content.decode()
    client.post(reverse("login-code"), {"phone": user.phone, "next": hidden_next(body)})
    response = client.post(reverse("login-code-verify"), {"code": code_from(sms.outbox[-1])})

    assert response.status_code == 302
    assert response.headers["Location"] == SEARCH_PAGE


@pytest.mark.parametrize(
    "hostile", ["//evil.example", "https://evil.example/", "javascript:alert(1)", "/\\evil.example"]
)
def test_a_hostile_next_lands_on_the_account_page(client, user, hostile):
    """The header link only ever carries a path on this site, so the login
    view's host check stays the one gate for a crafted link. Exactly
    `/account/`, not merely "this host": a browser reads `/\\` as `//`, and
    `urljoin` does not."""
    body = client.get(reverse("login-code") + "?" + urlencode({"next": hostile})).content.decode()
    assert hidden_next(body) == reverse("account")

    client.post(reverse("login-code"), {"phone": user.phone, "next": hostile})
    response = client.post(reverse("login-code-verify"), {"code": code_from(sms.outbox[-1])})

    assert response.headers["Location"] == reverse("account")


def test_the_sign_in_address_opens_on_the_code_form(client):
    """A customer signs in by code only, so /login/ opens the code form, and the
    destination comes with it (owner ruling 2026-11-04)."""
    response = client.get(reverse("login") + "?" + urlencode({"next": SEARCH_PAGE}))

    assert response.status_code == 302
    landing = urlparse(response.headers["Location"])
    assert landing.path == reverse("login-code")
    assert parse_qs(landing.query) == {"next": [SEARCH_PAGE]}
    assert hidden_next(client.get(response.headers["Location"]).content.decode()) == SEARCH_PAGE


def test_a_protected_page_sends_a_visitor_to_the_code_form(client):
    response = client.get(reverse("account"), follow=True)

    assert "accounts/login_code_request.html" in [used.name for used in response.templates]
    assert hidden_next(response.content.decode()) == reverse("account")


@pytest.mark.parametrize(
    "hostile", ["//evil.example", "https://evil.example/", "javascript:alert(1)", "/\\evil.example"]
)
def test_the_sign_in_address_leaves_a_hostile_next_to_the_one_gate(client, hostile):
    """The redirect copies the query string as it came, to a fixed path on this
    site; the code form's host check still decides where it may lead."""
    response = client.get(reverse("login") + "?" + urlencode({"next": hostile}), follow=True)

    hops = [urlparse(url) for url, _ in response.redirect_chain]
    assert [(hop.netloc, hop.path) for hop in hops] == [("", reverse("login-code"))]
    assert hidden_next(response.content.decode()) == reverse("account")


@pytest.mark.parametrize("name", ["login-code", "login-code-verify"])
def test_the_sign_in_pages_header_link_carries_no_next(client, name):
    """Signing in from a sign-in page would only return to the form just used."""
    if name == "login-code-verify":
        client.post(reverse("login-code"), {"phone": "09125550007"})

    assert header_sign_in_link(client, reverse(name) + "?next=/cart/") == reverse("login-code")


def test_an_empty_search_goes_to_the_shelf_instead_of_a_blank_page(client):
    """It used to render a heading over nothing, having fetched the whole
    catalogue first and thrown it away."""
    response = client.get(reverse("search"))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("home")


@pytest.mark.parametrize("method", ["code", "password"])
def test_shared_attempt_limit_does_not_blame_the_new_authentication_method(rf, method):
    phone = "09125550008"
    for _ in range(ratelimit.LOGIN_ATTEMPTS_PER_PHONE.times):
        assert ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_PHONE, phone)
    request = rf.post("/login/")
    if method == "code":
        with pytest.raises(services.RateLimited) as caught:
            services.complete_code_login(request, phone, "123456")
        message = caught.value.message
    else:
        message = services.password_attempt_refusal(request, phone)
    assert "برای این شماره چند بار پشت سر هم تلاش شده است." in message
    assert "۱۵ دقیقه" in message
    assert "اشتباه" not in message

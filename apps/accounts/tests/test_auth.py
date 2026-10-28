"""Auth is a money path: whoever holds a session can see delivered credentials.

Every test here names a way in that must stay shut, or a way in that must stay
open — a customer locked out by an over-tight limit is a refund, not a save.
"""

from __future__ import annotations

import json
import re
import smtplib
from urllib.parse import urljoin, urlparse

import pytest
import sentry_sdk
import time_machine
from django.contrib.auth.hashers import MD5PasswordHasher, make_password
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.test import Client
from django.urls import reverse
from sentry_sdk.transport import Transport

from apps.accounts import otp, services
from apps.accounts.models import User
from apps.core import ratelimit
from apps.core.email import render_email
from apps.core.observability import sentry_options
from apps.core.views import EMAIL_PREVIEWS

pytestmark = pytest.mark.django_db

PASSWORD = "CorrectHorse!2026"  # noqa: S105 — a fixture value, not a credential
START = 1_790_000_000  # a fixed instant, so time-travel tests never depend on the clock
PERSIAN_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
ASCII_TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


@pytest.fixture
def user():
    return User.objects.create_user("customer@example.test", PASSWORD, is_verified=True)


def code_from(message) -> str:
    """Pull the six digits out of the sent mail, in the digits it was rendered in."""
    found = re.search(r"[۰-۹]{6}", message.body)
    assert found, f"no six-digit code in the message body: {message.body[:200]}"
    return found.group(0).translate(PERSIAN_TO_ASCII)


# --- The model -------------------------------------------------------------


def test_email_case_insensitive_unique(user):
    """Ali@ and ali@ are one mailbox. Two accounts would mean one order stranded
    in each and a support conversation nobody wins."""
    with pytest.raises(IntegrityError), transaction.atomic():
        User(email="Customer@Example.TEST").save()


def test_the_manager_lowercases_on_the_way_in():
    created = User.objects.create_user("MiXeD@Example.COM")
    assert created.email == "mixed@example.com"


def test_a_user_created_without_a_password_cannot_be_logged_into_by_password():
    """An OTP-only account must not be reachable with an empty password."""
    created = User.objects.create_user("otponly@example.test")

    assert not created.has_usable_password()
    assert not created.check_password("")


def test_the_functional_index_is_the_guard_that_catches_case_variants():
    """The plain unique index catches exact duplicates. This asserts the FUNCTIONAL
    one exists, since it is the guard doing the work the plain one cannot."""
    constraint = next(
        c for c in User._meta.constraints if c.name == "user_email_case_insensitive_unique"
    )

    assert any(isinstance(expression, Lower) for expression in constraint.expressions)


# --- Password login --------------------------------------------------------


def test_login_email_password(client, user):
    response = client.post(reverse("login"), {"email": user.email, "password": PASSWORD})

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


def test_login_email_password_is_case_insensitive(client, user):
    client.post(reverse("login"), {"email": "CUSTOMER@EXAMPLE.TEST", "password": PASSWORD})

    assert client.session.get("_auth_user_id") == str(user.pk)


def test_a_wrong_password_does_not_say_which_half_was_wrong(client, user):
    response = client.post(reverse("login"), {"email": user.email, "password": "nope"})

    assert "_auth_user_id" not in client.session
    assert "ایمیل یا رمز عبور اشتباه است" in response.content.decode()


def test_an_inactive_account_cannot_log_in(client, user):
    user.is_active = False
    user.save(update_fields=["is_active"])

    client.post(reverse("login"), {"email": user.email, "password": PASSWORD})

    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("case", ["unknown", "wrong_password", "no_password", "inactive", "right"])
def test_every_password_attempt_costs_exactly_one_hash(client, user, monkeypatch, case):
    """If a miss on a real account costs different work from a miss on a stranger,
    the response time tells anyone which addresses shop here — and for this shop the
    customer list is itself sensitive. So every attempt, whatever its outcome, runs
    exactly one password hash.

    Counted, not timed: a timing assertion would be flaky, while the number of hash
    computations is exact and is what the time difference is made of. It was two for
    a real account with a wrong password, and two for an account with no password
    (Django already fakes one hash for those; the view added a second)."""
    monkeypatch.setattr(services, "_DUMMY_HASH", make_password("nobody-knows-this"))
    email, password = user.email, "not-the-password"
    if case == "unknown":
        email = "stranger@example.test"
    elif case == "no_password":
        email = User.objects.create_user("otponly@example.test").email
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

    client.post(reverse("login"), {"email": email, "password": password})

    assert len(hashes) == 1, f"{case}: {len(hashes)} hashes"


# --- OTP login -------------------------------------------------------------


def test_otp_login_creates_session_and_sets_verified(client):
    """The whole flow as a customer walks it: ask for a code, receive mail, enter it."""
    unverified = User.objects.create_user("new@example.test")
    assert not unverified.is_verified

    client.post(reverse("login-code"), {"email": unverified.email})
    assert len(mail.outbox) == 1

    response = client.post(reverse("login-code-verify"), {"code": code_from(mail.outbox[0])})

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(unverified.pk)
    unverified.refresh_from_db()
    assert unverified.is_verified, "receiving the code IS the verification (ADR-0012)"


def test_a_code_for_an_unknown_address_creates_the_account(client):
    """ADR-0012: one flow does login and registration, so there is no signup form
    and no user-enumeration oracle to probe."""
    assert not User.objects.filter(email="stranger@example.test").exists()

    client.post(reverse("login-code"), {"email": "stranger@example.test"})
    client.post(reverse("login-code-verify"), {"code": code_from(mail.outbox[0])})

    created = User.objects.get(email="stranger@example.test")
    assert created.is_verified
    assert not created.has_usable_password()


def test_a_code_is_single_use(client, user):
    client.post(reverse("login-code"), {"email": user.email})
    code = code_from(mail.outbox[0])
    client.post(reverse("login-code-verify"), {"code": code})
    client.post(reverse("logout"))

    client.post(reverse("login-code"), {"email": user.email})
    client.post(reverse("login-code-verify"), {"code": code})

    assert "_auth_user_id" not in client.session


def test_otp_expired_rejected(client, user):
    client.post(reverse("login-code"), {"email": user.email})
    code = code_from(mail.outbox[0])

    cache.clear()  # what a TTL expiry looks like from the code's point of view

    client.post(reverse("login-code-verify"), {"code": code})

    assert "_auth_user_id" not in client.session


def test_persian_digits_in_the_code_are_accepted(client, user):
    """The email renders ۴۱۸۳۰۵. A customer who copies exactly what they were shown
    must get in — otherwise the shop tells them, correctly and uselessly, that the
    code is wrong."""
    client.post(reverse("login-code"), {"email": user.email})
    persian = code_from(mail.outbox[0]).translate(ASCII_TO_PERSIAN)

    client.post(reverse("login-code-verify"), {"code": persian})

    assert client.session["_auth_user_id"] == str(user.pk)


def test_the_emailed_code_is_never_stored_in_the_clear(client, user):
    client.post(reverse("login-code"), {"email": user.email})
    code = code_from(mail.outbox[0])

    stored = cache.get(f"otp:{user.email}")

    assert stored is not None
    assert code not in str(stored)


# --- Rate limiting ---------------------------------------------------------


def test_otp_rate_limit_locks_after_n_attempts(client, user):
    """Three code requests per address per fifteen minutes (owner-approved).

    The limit protects two things: a six-digit code against guessing, and the
    SMTP2GO free tier's 200-a-day ceiling against being burned in a minute, which
    would silently lock out every other customer for the rest of the day.
    """
    for _ in range(ratelimit.OTP_REQUESTS_PER_EMAIL.times):
        client.post(reverse("login-code"), {"email": user.email})
    sent_before = len(mail.outbox)

    response = client.post(reverse("login-code"), {"email": user.email})

    assert len(mail.outbox) == sent_before, "a refused request must not still send mail"
    assert "پشت سر هم" in response.content.decode()


@pytest.mark.parametrize(
    ("url", "limit", "message"),
    [
        ("login-code", ratelimit.OTP_REQUESTS_PER_IP, services.TOO_MANY_CODES_FROM_NETWORK),
        ("login", ratelimit.LOGIN_ATTEMPTS_PER_IP, services.TOO_MANY_FROM_NETWORK),
    ],
)
def test_a_spent_network_budget_says_so_without_blaming_the_visitor(client, url, limit, message):
    """A mobile carrier puts many customers behind one address. The message for
    the budget per IP said «you have asked several times in a row» to whoever
    came next, and gave no number for the wait."""
    for n in range(limit.times):
        client.post(reverse(url), {"email": f"c{n}@example.test", "password": "wrong"})

    body = client.post(reverse(url), {"email": "next@example.test", "password": "x"})

    assert message in body.content.decode()


def test_the_code_is_burned_after_too_many_wrong_guesses(client, user):
    client.post(reverse("login-code"), {"email": user.email})
    code = code_from(mail.outbox[0])

    for _ in range(otp.MAX_ATTEMPTS):
        client.post(reverse("login-code-verify"), {"code": "000000"})

    client.post(reverse("login-code-verify"), {"code": code})

    assert "_auth_user_id" not in client.session, "the real code must die with the budget"


def test_a_guessed_out_code_stays_dead_when_its_counter_is_lost(user):
    """The count lives in a key of its own. Lost alone, to eviction or to a
    restore taken between the two writes, it gave a guessed-out code a fresh
    budget of guesses."""
    issued = otp.issue(user.email)
    issuance = cache.get(otp._key(user.email))
    wrong = f"{(int(issued.code) + 1) % 10**otp.CODE_LENGTH:0{otp.CODE_LENGTH}d}"
    for _ in range(otp.MAX_ATTEMPTS):
        otp.verify(user.email, wrong)

    cache.delete(otp._attempts_key(user.email, issuance))

    assert not otp.verify(user.email, issued.code)


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
    issued = otp.issue(user.email)
    issuance = cache.get(otp._key(user.email))
    window = WindowExpiredInsideIncr()
    monkeypatch.setattr(otp, "cache", window)

    assert not otp.verify(user.email, issued.code)

    [(key, timeout)] = window.touched
    assert key == otp._attempts_key(user.email, issuance)
    assert 0 < timeout <= otp.CODE_TTL_SECONDS


def test_a_wrong_guess_does_not_extend_the_codes_life(user):
    """Otherwise an attacker buys time simply by guessing at it. A guess a minute
    before the deadline must not keep the code alive past that deadline.

    The test this replaces only checked that the code still worked straight after
    a wrong guess, so it could not fail for the bug it was named after: on Redis,
    every wrong guess reset the code's life to five minutes."""
    with time_machine.travel(START, tick=False) as clock:
        issued = otp.issue(user.email)
        clock.shift(otp.CODE_TTL_SECONDS - 60)
        otp.verify(user.email, "000000")
        clock.shift(61)

        assert not otp.verify(user.email, issued.code), "the code outlived its deadline"


def test_a_wrong_guess_does_not_shorten_the_codes_life(user):
    """The page counts down ten minutes. A typo in the first second must not
    quietly make it five, while the countdown still shows time left."""
    with time_machine.travel(START, tick=False) as clock:
        issued = otp.issue(user.email)
        otp.verify(user.email, "000000")
        clock.shift(otp.CODE_TTL_SECONDS - 60)

        assert otp.verify(user.email, issued.code), "a typo cut the code's life short"


def test_a_successful_login_clears_the_attempt_budget(client, user):
    client.post(reverse("login"), {"email": user.email, "password": "wrong"})
    client.post(reverse("login"), {"email": user.email, "password": PASSWORD})

    assert ratelimit.remaining(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, user.email) == (
        ratelimit.LOGIN_ATTEMPTS_PER_EMAIL.times
    )


def test_the_email_limit_cannot_be_dodged_by_changing_case(client, user):
    for _ in range(ratelimit.OTP_REQUESTS_PER_EMAIL.times):
        client.post(reverse("login-code"), {"email": user.email})
    sent_before = len(mail.outbox)

    client.post(reverse("login-code"), {"email": user.email.upper()})

    assert len(mail.outbox) == sent_before, "holding down shift must not buy a new budget"


# --- The sign-in alert -----------------------------------------------------


def test_otp_login_on_passworded_account_sends_signin_alert(client, user):
    """The owner's own amendment at review round 1 (ADR-0012). It does not stop an
    attacker holding the mailbox — it makes the intrusion visible, which is the
    difference between an incident and a silent loss."""
    client.post(reverse("login-code"), {"email": user.email})
    client.post(reverse("login-code-verify"), {"code": code_from(mail.outbox[0])})

    assert len(mail.outbox) == 2
    alert = mail.outbox[1]
    assert alert.to == [user.email]
    assert "ورود" in alert.subject


def test_a_failed_signin_alert_does_not_fail_the_login(client, user, monkeypatch, caplog):
    """The docstring always promised this and nothing enforced it. By the time the
    alert is sent the code is already spent, so a relay error or the 200-a-day cap
    turning into an error page would lock a customer out over our mail problem.
    The failure must still be visible — logged, never swallowed."""
    real_send = services.send_templated_email

    def alert_fails(name, **kwargs):
        if name == "signin_alert":
            raise smtplib.SMTPServerDisconnected("relay went away")
        return real_send(name, **kwargs)

    monkeypatch.setattr(services, "send_templated_email", alert_fails)
    client.post(reverse("login-code"), {"email": user.email})

    response = client.post(reverse("login-code-verify"), {"code": code_from(mail.outbox[0])})

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
    assert "sign-in alert" in caplog.text


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


def test_a_failed_signin_alert_reaches_error_tracking_without_the_address(
    client, user, monkeypatch, caplog, error_reports
):
    """The comment above the fix promised two things: the failure is never
    silent, and what is logged names the account, not the person. Checked against
    production's own error-reporting settings, both were false. The failure was
    logged at WARNING, which error tracking keeps only as a breadcrumb — no issue
    anyone would see. And it was logged with its traceback, whose text quotes the
    recipient: an SMTP "recipients refused" error is a dict keyed by the address,
    and relays echo it in their replies."""
    real_send = services.send_templated_email

    def relay_refuses_the_alert(name, **kwargs):
        if name == "signin_alert":
            reply = f"5.1.1 <{user.email}>: Recipient address rejected".encode()
            raise smtplib.SMTPRecipientsRefused({user.email: (550, reply)})
        return real_send(name, **kwargs)

    monkeypatch.setattr(services, "send_templated_email", relay_refuses_the_alert)
    client.post(reverse("login-code"), {"email": user.email})
    code = code_from(mail.outbox[0])

    client.post(reverse("login-code-verify"), {"code": code})

    assert client.session["_auth_user_id"] == str(user.pk), "the login itself must succeed"
    alerts = [r for r in error_reports if "sign-in alert not sent" in json.dumps(r)]
    everything = json.dumps(error_reports) + caplog.text
    broken = [
        promise
        for promise, kept in [
            ("a failed alert becomes an issue in error tracking", len(alerts) == 1),
            (
                "the customer's address appears in no log and no report",
                user.email not in everything,
            ),
            ("the login code appears in no log and no report", code not in everything),
        ]
        if not kept
    ]
    assert not broken, f"broken promises: {broken}"


def test_the_alert_takes_the_customer_to_the_password_page(client, user, settings):
    """The button and the text version's link went to /account/, which for an
    account with a password has no password form on it: a worried customer had
    to find «رمز عبور» in the menu themselves."""
    settings.SITE_URL = "https://premshop.ir"
    client.post(reverse("login-code"), {"email": user.email})
    client.post(reverse("login-code-verify"), {"code": code_from(mail.outbox[0])})

    alert = mail.outbox[1]
    password_page = "https://premshop.ir" + reverse("account-password")
    assert f'href="{password_page}"' in alert.alternatives[0][0], "the button"
    assert password_page in alert.body, "the text version's link"


def test_the_alert_link_is_the_shops_address_whatever_host_the_request_named(
    client, user, settings
):
    """The Host header is the client's to choose. Built on it, the link in a
    security notice pointed wherever the request said the site was."""
    settings.SITE_URL = "https://premshop.ir"
    settings.ALLOWED_HOSTS = ["*"]
    elsewhere = {"HTTP_HOST": "attacker.example"}
    client.post(reverse("login-code"), {"email": user.email}, **elsewhere)
    client.post(reverse("login-code-verify"), {"code": code_from(mail.outbox[0])}, **elsewhere)

    alert = mail.outbox[1]
    sent = alert.body + alert.alternatives[0][0]
    assert "https://premshop.ir" + reverse("account-password") in sent
    assert "attacker.example" not in sent


def test_the_alert_preview_shows_the_same_destination():
    _, text, html = render_email("signin_alert", EMAIL_PREVIEWS["signin_alert"])

    assert 'href="https://premshop.ir/account/password/"' in html
    assert "https://premshop.ir/account/password/" in text


def visible(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_alert_says_when_and_what_to_do_in_order():
    """Someone who can read the mailbox can ask for a code and sign straight back
    in after the shop password changes, so the mailbox comes first. And «which
    day» alone cannot tell a customer whether it was their own sign-in an hour
    ago."""
    _, text, html = render_email("signin_alert", EMAIL_PREVIEWS["signin_alert"])

    for part in (" ".join(text.split()), visible(html)):
        assert "۲۷ شهریور ۱۴۰۵، ساعت ۱۴:۳۲" in part, "11:02 UTC is 14:32 in Tehran"
        steps = [part.find(step) for step in STEPS]
        assert -1 not in steps and steps == sorted(steps), dict(zip(STEPS, steps, strict=True))
        assert "از همه‌ی دستگاه‌های دیگر خارج می‌شوید" in part


STEPS = ("اول رمز ایمیلتان", "بعد رمز عبور PremShop", "به پشتیبانی خبر بدهید")


def test_the_alert_carries_no_amber():
    """Amber means a clock is running, and nothing in this mail is timed."""
    _, _, html = render_email("signin_alert", EMAIL_PREVIEWS["signin_alert"])

    for amber in ("#FDF6EC", "#F0DFC4", "#92400E"):
        assert amber not in html.upper()


def test_changing_the_password_signs_out_every_other_device(client, user):
    """The alert promises this, so it is checked rather than assumed. Django
    keys each session to a hash of the password; the view re-signs in only the
    tab that made the change."""
    elsewhere = Client()
    elsewhere.force_login(user)
    client.force_login(user)

    client.post(
        reverse("account-password"),
        {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
    )

    assert client.get(reverse("account")).status_code == 200, "this tab stays signed in"
    assert elsewhere.get(reverse("account")).status_code == 302, "the other device is signed out"


NEW_PASSWORD = "Another-Horse!2027"  # noqa: S105 — a fixture value, not a credential


def test_no_signin_alert_for_an_account_with_no_password(client):
    """An OTP-only account has no second factor for the notice to protect, so it
    would be noise on every ordinary login."""
    User.objects.create_user("nopass@example.test")

    client.post(reverse("login-code"), {"email": "nopass@example.test"})
    client.post(reverse("login-code-verify"), {"code": code_from(mail.outbox[0])})

    assert len(mail.outbox) == 1


# --- Sessions and pages ----------------------------------------------------


def test_login_rotates_the_session_key(client, user):
    """Session fixation: an attacker who plants a known session id in the browser
    before login must not be able to reuse it afterwards."""
    client.session.create()
    before = client.session.session_key

    client.post(reverse("login"), {"email": user.email, "password": PASSWORD})

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


@pytest.mark.parametrize("name", ["login", "login-code"])
def test_every_public_auth_page_renders(client, name):
    """A smoke test, because a missing {% load %} is a 500 no unit test sees and
    the first customer to reach the page finds instead. This caught exactly that
    on the account page."""
    response = client.get(reverse(name))

    assert response.status_code == 200
    assert (
        "PremShop" in response.content.decode()
    ), "the wordmark is the brand ruling: Latin, everywhere"


@pytest.mark.parametrize(
    "name", ["login", "login-code", "login-code-verify", "account", "account-password"]
)
def test_sign_in_and_account_pages_ask_not_to_be_indexed(client, user, name):
    """Nothing on them is worth a search result, and a results page that lands
    a stranger on a code-entry form is a phishing kit's favourite shape."""
    if name.startswith("account"):
        client.force_login(user)
    elif name == "login-code-verify":
        client.post(reverse("login-code"), {"email": user.email})

    response = client.get(reverse(name))

    assert response.status_code == 200
    assert '<meta name="robots" content="noindex">' in response.content.decode()


def test_the_account_page_renders_for_a_signed_in_user(client, user):
    client.force_login(user)

    response = client.get(reverse("account"))

    assert response.status_code == 200
    body = response.content.decode()
    assert user.email in body
    assert "رمز عبور" in body, "the dashboard must offer the password section"


def test_an_otp_only_user_is_not_asked_for_a_current_password(client):
    """Asking would be an unanswerable question on the page meant to fix it."""
    passwordless = User.objects.create_user("otponly2@example.test")
    client.force_login(passwordless)

    body = client.get(reverse("account-password")).content.decode()

    assert "رمز عبور فعلی" not in body


def test_no_password_input_is_ever_rendered_without_the_field_class(client, user):
    """Tailwind's preflight sets border-width: 0 on every element, so an input
    without field__input has NO BORDER — invisible on screen, still focusable and
    submittable. Nothing errors; the page silently becomes unusable. This shipped
    on the account page and was caught by looking at a screenshot, not a test."""
    client.force_login(user)

    body = client.get(reverse("account-password")).content.decode()

    for match in re.finditer(r"<input[^>]*type=\"password\"[^>]*>", body):
        assert "field__input" in match.group(0), match.group(0)


# --- Where you land after signing in --------------------------------------


def test_password_login_returns_you_to_the_page_you_asked_for(client, user):
    """`@login_required` puts the wanted page in `?next=`. Both login views
    used to discard it and send everyone to /account/."""
    response = client.post(
        reverse("login") + "?next=/account/password/",
        {"email": user.email, "password": PASSWORD, "next": "/account/password/"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/account/password/"


def test_code_login_returns_you_to_the_page_you_asked_for(client):
    """The code flow spans three requests, so the destination rides the
    session rather than the URL."""
    client.post(
        reverse("login-code") + "?next=/account/password/",
        {"email": "lands@example.test", "next": "/account/password/"},
    )
    code = code_from(mail.outbox[-1])

    response = client.post(reverse("login-code-verify"), {"code": code})

    assert response.status_code == 302
    assert response.headers["Location"] == "/account/password/"


def test_the_json_path_reports_the_same_destination(client):
    client.post(
        reverse("login-code") + "?next=/account/password/",
        {"email": "json@example.test", "next": "/account/password/"},
    )
    code = code_from(mail.outbox[-1])

    response = client.post(
        reverse("login-code-verify"), {"code": code}, headers={"accept": "application/json"}
    )

    assert json.loads(response.content)["next"] == "/account/password/"


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
@pytest.mark.parametrize("route", ["login", "login-code"])
def test_next_cannot_send_anyone_off_this_site(client, user, hostile, route):
    """Without the host check `?next=` turns a login page into an open
    redirect, which is worth more to a phisher on the shop's own domain than
    on anyone else's.

    The assertion resolves the Location against this origin with `urljoin`
    rather than asking Django's own validator again — a check that reuses the
    value under test cannot see that the value is wrong. A backslash form is
    allowed through deliberately: browsers read it as a path separator, so it
    lands on a path of *this* site, which is a 404 and not a phishing hop.
    """
    payload = {"next": hostile}
    if route == "login":
        payload |= {"email": user.email, "password": PASSWORD}
    else:
        payload |= {"email": "hostile@example.test"}

    response = client.post(reverse(route), payload)
    assert response.status_code == 302

    landing = response.headers["Location"]
    if route == "login-code":
        # The code flow stores it; follow through to where it actually sends you.
        code = code_from(mail.outbox[-1])
        landing = client.post(reverse("login-code-verify"), {"code": code}).headers["Location"]

    assert urlparse(urljoin("http://testserver/", landing)).netloc == "testserver", landing


def test_an_empty_search_goes_to_the_shelf_instead_of_a_blank_page(client):
    """It used to render a heading over nothing, having fetched the whole
    catalogue first and thrown it away."""
    response = client.get(reverse("search"))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("home")

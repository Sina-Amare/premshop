"""Auth is a money path: whoever holds a session can see delivered credentials.

Every test here names a way in that must stay shut, or a way in that must stay
open — a customer locked out by an over-tight limit is a refund, not a save.
"""

from __future__ import annotations

import re

import pytest
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.urls import reverse

from apps.accounts import otp, ratelimit
from apps.accounts.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "CorrectHorse!2026"  # noqa: S105 — a fixture value, not a credential
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


def test_the_code_is_burned_after_too_many_wrong_guesses(client, user):
    client.post(reverse("login-code"), {"email": user.email})
    code = code_from(mail.outbox[0])

    for _ in range(otp.MAX_ATTEMPTS):
        client.post(reverse("login-code-verify"), {"code": "000000"})

    client.post(reverse("login-code-verify"), {"code": code})

    assert "_auth_user_id" not in client.session, "the real code must die with the budget"


def test_a_wrong_guess_does_not_extend_the_codes_life(user):
    """Otherwise an attacker buys unlimited time simply by guessing at it."""
    issued = otp.issue(user.email)

    otp.verify(user.email, "000000")

    assert cache.get(f"otp:{user.email}") is not None
    assert otp.verify(user.email, issued.code), "a real code must still work"


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


def test_the_account_page_renders_for_a_signed_in_user(client, user):
    client.force_login(user)

    response = client.get(reverse("account"))

    assert response.status_code == 200
    body = response.content.decode()
    assert user.email in body
    assert "رمز عبور" in body, "the password form must be there and use the site's wording"


def test_an_otp_only_user_is_not_asked_for_a_current_password(client):
    """Asking would be an unanswerable question on the page meant to fix it."""
    passwordless = User.objects.create_user("otponly2@example.test")
    client.force_login(passwordless)

    body = client.get(reverse("account")).content.decode()

    assert "رمز عبور فعلی" not in body


def test_no_password_input_is_ever_rendered_without_the_field_class(client, user):
    """Tailwind's preflight sets border-width: 0 on every element, so an input
    without field__input has NO BORDER — invisible on screen, still focusable and
    submittable. Nothing errors; the page silently becomes unusable. This shipped
    on the account page and was caught by looking at a screenshot, not a test."""
    client.force_login(user)

    body = client.get(reverse("account")).content.decode()

    for match in re.finditer(r"<input[^>]*type=\"password\"[^>]*>", body):
        assert "field__input" in match.group(0), match.group(0)

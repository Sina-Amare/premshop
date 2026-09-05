"""The code-entry page after the S2 review: six cells, a countdown, resend.

The cells are JavaScript; what the server owes them is a JSON answer on the same
view, an expiry it can count down to, and a resend route. Those three are what
these tests pin. The single hidden field the cells write into is the same field
test_auth.py has always posted to, so nothing there changed.
"""

from __future__ import annotations

import re
import time

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts import otp
from apps.accounts.models import User
from apps.accounts.tests.test_auth import PASSWORD, code_from
from apps.accounts.views import PENDING_EXPIRES_KEY, RESEND_COOLDOWN_SECONDS
from apps.core import ratelimit

pytestmark = pytest.mark.django_db

JSON = {"HTTP_ACCEPT": "application/json"}


@pytest.fixture
def user():
    return User.objects.create_user("customer@example.test", PASSWORD, is_verified=True)


@pytest.fixture
def waiting(client, user):
    """A client that has asked for a code and is on the entry page."""
    client.post(reverse("login-code"), {"email": user.email})
    return client


# --- The expiry the page counts down to ------------------------------------


def test_requesting_a_code_records_when_it_expires(waiting):
    expires_at = waiting.session[PENDING_EXPIRES_KEY]

    assert abs(expires_at - (time.time() + otp.CODE_TTL_SECONDS)) < 5


def test_the_page_carries_the_expiry_and_the_cooldown(waiting):
    response = waiting.get(reverse("login-code-verify"))
    body = response.content.decode()

    left = int(re.search(r'data-expires-in="(\d+)"', body).group(1))
    assert 0 < left <= otp.CODE_TTL_SECONDS, "seconds remaining, not an absolute time"
    assert f'data-resend-cooldown="{RESEND_COOLDOWN_SECONDS}"' in body
    assert reverse("login-code-resend") in body


def test_the_plain_input_is_still_there_for_a_browser_without_javascript(waiting):
    """Progressive enhancement: the cells are an enhancement of a field that
    works on its own. If this disappears, a JS-off customer has no way in."""
    body = waiting.get(reverse("login-code-verify")).content.decode()

    assert 'name="code"' in body
    assert body.count('class="otp__cell"') == otp.CODE_LENGTH


# --- The JSON answer the cells submit for ----------------------------------


def test_a_correct_code_answers_json_with_where_to_go(waiting, user):
    code = code_from(mail.outbox[0])

    response = waiting.post(reverse("login-code-verify"), {"code": code}, **JSON)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "next": reverse("account")}
    assert waiting.session["_auth_user_id"] == str(user.pk)
    assert PENDING_EXPIRES_KEY not in waiting.session, "the countdown must die with the code"


def test_a_wrong_code_answers_json_without_leaving_the_page(waiting):
    response = waiting.post(reverse("login-code-verify"), {"code": "000000"}, **JSON)

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"]
    assert "_auth_user_id" not in waiting.session


def test_a_malformed_code_answers_json_with_the_form_error(waiting):
    response = waiting.post(reverse("login-code-verify"), {"code": "12"}, **JSON)

    assert response.json() == {"ok": False, "error": "کد باید ۶ رقم باشد."}


def test_too_many_guesses_answer_429_in_json(waiting):
    for _ in range(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL.times):
        waiting.post(reverse("login-code-verify"), {"code": "000000"}, **JSON)

    response = waiting.post(reverse("login-code-verify"), {"code": "000000"}, **JSON)

    assert response.status_code == 429
    assert response.json()["ok"] is False
    assert "پشت سر هم" in response.json()["error"]


def test_without_the_accept_header_the_view_still_answers_html(waiting):
    """The same view serves both; a browser with JS off must get a page, not JSON."""
    response = waiting.post(reverse("login-code-verify"), {"code": "000000"})

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")


# --- Resend ----------------------------------------------------------------


def test_resend_replaces_the_code_and_restarts_the_clock(waiting, user):
    first_code = code_from(mail.outbox[0])
    first_expiry = waiting.session[PENDING_EXPIRES_KEY]

    response = waiting.post(reverse("login-code-resend"), **JSON)

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["cooldown"] == RESEND_COOLDOWN_SECONDS
    assert 0 < data["expires_in"] <= otp.CODE_TTL_SECONDS
    assert waiting.session[PENDING_EXPIRES_KEY] >= first_expiry
    assert len(mail.outbox) == 2
    second_code = code_from(mail.outbox[1])

    assert not otp.verify(user.email, first_code), "the old code must be dead"
    assert otp.verify(user.email, second_code), "the new one must work"


def test_resend_is_rate_limited_like_a_first_request(waiting):
    """The 60-second cooldown is a courtesy in the browser; this is the defence."""
    for _ in range(ratelimit.OTP_REQUESTS_PER_EMAIL.times - 1):
        waiting.post(reverse("login-code-resend"), **JSON)
    sent_before = len(mail.outbox)

    response = waiting.post(reverse("login-code-resend"), **JSON)

    assert response.status_code == 429
    assert len(mail.outbox) == sent_before


def test_resend_without_a_pending_address_is_refused(client):
    response = client.post(reverse("login-code-resend"), **JSON)

    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_resend_refuses_get(waiting):
    assert waiting.get(reverse("login-code-resend")).status_code == 405


# --- The account page additions --------------------------------------------


def test_the_password_hint_names_the_rules(client, user):
    from apps.accounts.forms import PASSWORD_HINT

    client.force_login(user)
    body = client.get(reverse("account")).content.decode()

    assert PASSWORD_HINT in body
    assert "۸" in PASSWORD_HINT, "the minimum length in settings is 8"


@pytest.mark.parametrize(
    "typed, stored",
    [
        ("09123456789", "09123456789"),
        ("۰۹۱۲۳۴۵۶۷۸۹", "09123456789"),
        ("+98 912 345 6789", "09123456789"),
        ("0912-345-6789", "09123456789"),
        ("", ""),
    ],
)
def test_the_phone_is_normalised_on_the_way_in(client, user, typed, stored):
    client.force_login(user)

    client.post(reverse("account"), {"form": "profile", "phone": typed})

    user.refresh_from_db()
    assert user.phone == stored


def test_a_phone_that_is_not_an_iranian_mobile_is_refused(client, user):
    client.force_login(user)

    response = client.post(reverse("account"), {"form": "profile", "phone": "02122334455"})

    user.refresh_from_db()
    assert user.phone == ""
    assert "۱۱ رقم" in response.content.decode()


def test_saving_the_phone_does_not_touch_the_password_form(client, user):
    """Two forms, one page: a submit to one must not paint the other red."""
    client.force_login(user)

    body = client.post(reverse("account"), {"form": "profile", "phone": "12345"}).content.decode()

    assert "رمز عبور فعلی" in body
    assert body.count("field__error") == 1


# --- The header ------------------------------------------------------------


def test_the_header_icon_goes_to_login_when_signed_out(client):
    body = client.get(reverse("login")).content.decode()

    assert f'href="{reverse("login-code")}" class="hdr__icon"' in body


def test_the_header_icon_goes_to_the_account_when_signed_in(client, user):
    client.force_login(user)

    body = client.get(reverse("account")).content.decode()

    assert f'href="{reverse("account")}" class="hdr__icon"' in body

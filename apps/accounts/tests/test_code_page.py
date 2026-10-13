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
    body = client.get(reverse("account-password")).content.decode()

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

    client.post(reverse("account"), {"phone": typed})

    user.refresh_from_db()
    assert user.phone == stored


def test_a_phone_that_is_not_an_iranian_mobile_is_refused(client, user):
    client.force_login(user)

    response = client.post(reverse("account"), {"phone": "02122334455"})

    user.refresh_from_db()
    assert user.phone == ""
    assert "۱۱ رقم" in response.content.decode()


def test_a_rejected_phone_paints_one_field_and_nothing_else(client, user):
    """The password form lives on its own page, so a phone error cannot reach
    it. What must still hold is that exactly one field is marked."""
    client.force_login(user)

    body = client.post(reverse("account"), {"phone": "12345"}).content.decode()

    assert "رمز عبور فعلی" not in body
    assert body.count("field__error") == 1


# --- The header ------------------------------------------------------------


def test_the_header_account_link_goes_to_login_when_signed_out(client):
    body = client.get(reverse("login")).content.decode()

    assert f'href="{reverse("login-code")}" class="hdr__account"' in body
    # Labelled, not a bare pictogram: a first-time visitor has to be able to
    # see where signing up happens without decoding a glyph.
    assert "ورود یا ثبت‌نام" in body


def test_the_header_account_link_goes_to_the_account_when_signed_in(client, user):
    client.force_login(user)

    body = client.get(reverse("account")).content.decode()

    assert f'href="{reverse("account")}" class="hdr__account"' in body
    assert "ورود یا ثبت‌نام" not in body


# --- The account dashboard ---------------------------------------------------


@pytest.mark.parametrize(
    ("name", "heading"),
    [("account", "اطلاعات حساب"), ("account-password", "رمز عبور")],
)
def test_every_dashboard_section_has_its_own_page(client, user, name, heading):
    client.force_login(user)

    response = client.get(reverse(name))

    assert response.status_code == 200
    assert heading in response.content.decode()


def test_the_old_profile_address_still_goes_somewhere(client, user):
    """The two account pages merged on 2026-10-14. The address was live, and a
    404 on a page someone bookmarked is a worse answer than the page they
    wanted — so it redirects rather than disappearing."""
    client.force_login(user)

    response = client.get(reverse("account-profile"))

    assert response.status_code == 302
    assert response["Location"] == reverse("account")


@pytest.mark.parametrize("name", ["account", "account-password"])
def test_a_dashboard_section_requires_a_session(client, name):
    assert client.get(reverse(name)).status_code == 302


def test_one_fact_lives_in_one_place(client, user):
    """The phone number used to be printed on an overview page and edited on
    another. One fact in two places means no way to tell which is
    authoritative, and two pages to keep in step."""
    # Not the number in the field's own example, which would collide.
    user.phone = "09351112233"
    user.save()
    client.force_login(user)

    body = client.get(reverse("account")).content.decode()

    assert body.count("09351112233") == 1, "the stored number is rendered more than once"
    assert 'value="09351112233"' in body, "and the one place is the field that edits it"


def test_the_navigation_marks_exactly_one_current_section(client, user):
    from apps.catalog.models import Category, Product

    # With a department in the header the shop nav renders too, which is where
    # a second aria-current can hide: «همه‌ی محصولات» lit up on every page that
    # was not a category, including this one.
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    Product.objects.create(
        category=category,
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        delivery_hours=12,
        status=Product.Status.ACTIVE,
    )
    client.force_login(user)

    body = client.get(reverse("account")).content.decode()

    assert body.count('aria-current="page"') == 1
    assert f'href="{reverse("account")}" aria-current="page"' in body


def test_the_dashboard_does_not_advertise_what_is_not_built(client, user):
    """Orders arrive at S4b and subscriptions at S9. A menu entry that leads
    nowhere on a shop that already takes money reads as an unfinished shop."""
    client.force_login(user)

    body = client.get(reverse("account")).content.decode()

    for unbuilt in ["سفارش‌های من", "اشتراک‌های من", "تیکت", "کیف پول", "به‌زودی"]:
        assert unbuilt not in body, unbuilt


def test_the_dashboard_does_not_score_the_customer_against_things_they_cannot_do(client, user):
    """A completeness meter was designed and cut. Of the four things such a
    meter would count, email-verified is true for every account from birth
    (services.py: the code proved the mailbox) and Telegram linking is S8 with
    no view behind it — so the bar would sit at ۲ از ۴ forever with two dead
    actions under it. A progress meter nobody can finish is a chore the shop
    invented for its customer, on the one page where it should be answering
    questions instead."""
    client.force_login(user)

    body = client.get(reverse("account")).content.decode()

    for invented in ["از ۴", "تکمیل حساب", "تلگرام شما", "درصد"]:
        assert invented not in body, invented


def test_the_dashboard_does_not_report_the_current_session_as_the_last_one(client, user):
    """Django writes last_login at the moment of login, so «آخرین ورود» on
    this page would always read «لحظاتی پیش» — and everyone who has met that
    label on a bank site reads it as the *previous* session. Showing it needs
    a second timestamp the model does not have."""
    client.force_login(user)

    body = client.get(reverse("account")).content.decode()

    assert "آخرین ورود" not in body


def test_the_dashboard_names_one_next_step_at_a_time(client, user):
    """A list of three optional chores teaches the customer to ignore the
    page. The shop picks the single most useful thing that is actually
    missing, and when nothing is missing it says so instead of inventing."""
    client.force_login(user)
    assert user.has_usable_password()

    body = client.get(reverse("account")).content.decode()

    # Nothing to prompt: this account can already sign in both ways, and the
    # phone is an ordinary editable field rather than a chore to nag about.
    assert 'class="acct__next"' not in body


def test_an_otp_only_account_is_offered_a_password_and_nothing_else(client):
    """An account created by a code login has no password. That is the single
    step worth naming for them, and it is the only one — a dashboard that
    lists three optional chores teaches the customer to ignore it."""
    only_code = User.objects.create_user("codeonly@example.test", None, is_verified=True)
    client.force_login(only_code)

    body = client.get(reverse("account")).content.decode()

    assert body.count('class="acct__next"') == 1
    assert "ثبت رمز عبور" in body

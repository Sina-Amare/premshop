"""One way in per account: a customer signs in by code, staff by the panel's password.

A staff session opens the operator panel, which checks only `is_staff`, so a code that
reaches a staff account's inbox must not sign it in. A customer has no password, so the
shop has nothing to change, recover or send an alert about.
"""

from __future__ import annotations

import re

import pytest
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse

from apps.accounts import views
from apps.accounts.models import User
from apps.core import sms

pytestmark = pytest.mark.django_db

PASSWORD = "CorrectHorse!2026"  # noqa: S105 — a fixture value, not a credential
STAFF, CUSTOMER = "09123330001", "09123330002"


def texted_code() -> str:
    found = re.search(r"\b\d{6}\b", sms.outbox[-1][1])
    assert found, "no code in the sign-in message"
    return found.group(0)


def sign_in_by_code(client, phone: str):
    client.post(reverse("login-code"), {"phone": phone})
    return client.post(reverse("login-code-verify"), {"code": texted_code()})


def test_a_staff_account_cannot_sign_in_on_the_shop_by_code(client):
    User.objects.create_user("operator@example.test", PASSWORD, phone=STAFF, is_staff=True)

    response = sign_in_by_code(client, STAFF)

    assert "_auth_user_id" not in client.session
    assert views.WRONG_CODE in response.content.decode()


@pytest.mark.parametrize("answer", ["page", "json"])
def test_a_staff_accounts_right_code_is_answered_as_a_wrong_one(client, answer):
    """The same status and words as a wrong code, on the page and on checkout's
    script path, so probing with codes cannot list the staff addresses."""
    accept = {"HTTP_ACCEPT": "application/json"} if answer == "json" else {}
    User.objects.create_user("operator@example.test", PASSWORD, phone=STAFF, is_staff=True)
    client.post(reverse("login-code"), {"phone": STAFF})
    right = texted_code()
    wrong = f"{(int(right) + 1) % 10**6:06d}"

    refused = client.post(reverse("login-code-verify"), {"code": wrong}, **accept)
    staff = client.post(reverse("login-code-verify"), {"code": right}, **accept)

    assert staff.status_code == refused.status_code
    if answer == "json":
        assert staff.json() == refused.json()
    else:
        assert views.WRONG_CODE in staff.content.decode()


def test_a_code_session_never_opens_the_panel_even_once_the_account_is_staff(client):
    """The panel checks only is_staff, so a session a code opened must carry its
    own mark: ticking is_staff on a customer's account later does not change it."""
    customer = User.objects.create_user(phone=CUSTOMER)
    sign_in_by_code(client, CUSTOMER)
    User.objects.filter(pk=customer.pk).update(is_staff=True, is_superuser=True)

    response = client.get(reverse("admin:index"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("admin:login"))


def test_a_code_sign_in_sends_no_mail_even_to_an_account_with_a_password(client):
    User.objects.create_user("customer@example.test", PASSWORD, phone=CUSTOMER)

    sign_in_by_code(client, CUSTOMER)

    assert "_auth_user_id" in client.session
    assert not mail.outbox, [message.subject for message in mail.outbox]


@pytest.mark.parametrize(
    "name", ["login-password", "account-password", "account-password-recovery"]
)
def test_the_shop_has_no_password_pages(name):
    with pytest.raises(NoReverseMatch):
        reverse(name)


@pytest.mark.parametrize(
    "path", ["/login/password/", "/account/password/", "/account/password/recovery/"]
)
def test_the_old_password_addresses_are_not_found(client, path):
    client.force_login(User.objects.create_user("customer@example.test", PASSWORD))

    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "new, expected",
    [
        ("Ab1!", "رمز عبور باید حداقل ۸ کاراکتر باشد."),
        ("123456789", "رمز عبور نباید فقط عدد باشد."),
        ("password", "این رمز عبور خیلی رایج است."),
        ("operator@example.test", "رمز عبور خیلی شبیه ایمیل شماست."),
    ],
)
def test_the_panels_password_rules_speak_the_shops_words(client, new, expected):
    """Staff change their password on the panel's own page, where these four rules
    are the shop's validators and not Django's wording."""
    client.force_login(User.objects.create_user("operator@example.test", PASSWORD, is_staff=True))

    body = client.post(
        reverse("admin:password_change"),
        {"old_password": PASSWORD, "new_password1": new, "new_password2": new},
    ).content.decode()

    assert expected in body


def test_saving_the_account_page_writes_only_its_own_column(client):
    """A stale account form must not write back a password hash changed meanwhile."""
    client.force_login(User.objects.create_user("operator@example.test", PASSWORD, is_staff=True))

    with CaptureQueriesContext(connection) as queries:
        client.post(reverse("account"), {"email": "operator@example.test"})

    updates = [q["sql"] for q in queries if q["sql"].startswith('UPDATE "accounts_user"')]
    assert updates and all('"password"' not in sql for sql in updates), updates

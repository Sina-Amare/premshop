"""One way in per account: a customer signs in by code, staff by the panel's password.

A staff session opens the operator panel, which checks only `is_staff`, so a code that
reaches a staff account's inbox must not sign it in. A customer has no password, so the
shop has nothing to change, recover or send an alert about.
"""

from __future__ import annotations

import re

import pytest
from django.core import mail
from django.urls import NoReverseMatch, reverse

from apps.accounts import views
from apps.accounts.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "CorrectHorse!2026"  # noqa: S105 — a fixture value, not a credential
PERSIAN_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def sign_in_by_code(client, email: str):
    client.post(reverse("login-code"), {"email": email})
    found = re.search(r"[۰-۹]{6}", mail.outbox[-1].body)
    assert found, "no code in the sign-in mail"
    return client.post(
        reverse("login-code-verify"), {"code": found.group(0).translate(PERSIAN_TO_ASCII)}
    )


def test_a_staff_account_cannot_sign_in_on_the_shop_by_code(client):
    User.objects.create_user("operator@example.test", PASSWORD, is_staff=True)

    response = sign_in_by_code(client, "operator@example.test")

    assert "_auth_user_id" not in client.session
    assert views.WRONG_CODE in response.content.decode()


def test_a_code_sign_in_sends_no_alert_even_to_an_account_with_a_password(client):
    User.objects.create_user("customer@example.test", PASSWORD)

    sign_in_by_code(client, "customer@example.test")

    assert "_auth_user_id" in client.session
    assert len(mail.outbox) == 1, [message.subject for message in mail.outbox]


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

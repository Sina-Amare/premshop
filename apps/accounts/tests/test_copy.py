"""One word for one thing, on every screen.

Django's own Persian says «گذرواژه»; this shop says «رمز عبور». The hint under the
field, the error beside it and the confirmation above it must agree, or the page
reads as two systems bolted together. These pin the places Django's wording
would otherwise leak through.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.test_auth import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return User.objects.create_user("copy@example.test", PASSWORD, is_verified=True)


def change_password(client, old: str, new: str) -> str:
    return client.post(
        reverse("account"),
        {"form": "password", "old_password": old, "new_password1": new, "new_password2": new},
    ).content.decode()


@pytest.mark.parametrize(
    "new, expected",
    [
        ("Ab1!", "رمز عبور باید حداقل ۸ کاراکتر باشد."),
        ("123456789", "رمز عبور نباید فقط عدد باشد."),
        ("password", "این رمز عبور خیلی رایج است."),
        ("copy@example.test", "رمز عبور خیلی شبیه ایمیل شماست."),
    ],
)
def test_every_password_rule_speaks_the_shops_words(client, user, new, expected):
    client.force_login(user)

    body = change_password(client, PASSWORD, new)

    assert expected in body
    assert "گذرواژه" not in body, "Django's own wording leaked through"


def test_a_wrong_current_password_speaks_the_shops_words(client, user):
    client.force_login(user)

    body = change_password(client, "not-it", "Str0ng-enough-pass")

    assert "رمز عبور فعلی اشتباه است." in body
    assert "گذرواژه" not in body


def test_a_mismatched_repeat_speaks_the_shops_words(client, user):
    client.force_login(user)

    body = client.post(
        reverse("account"),
        {
            "form": "password",
            "old_password": PASSWORD,
            "new_password1": "Str0ng-enough-pass",
            "new_password2": "Str0ng-enough-pasS",
        },
    ).content.decode()

    assert "تکرار رمز عبور یکسان نیست." in body
    assert "گذرواژه" not in body


@pytest.mark.parametrize("name", ["login", "login-code"])
def test_no_page_says_gozarvazheh(client, name):
    assert "گذرواژه" not in client.get(reverse(name)).content.decode()

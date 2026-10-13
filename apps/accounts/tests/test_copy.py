"""One word for one thing, on every screen.

Django's own Persian says «گذرواژه»; this shop says «رمز عبور». The hint under the
field, the error beside it and the confirmation above it must agree, or the page
reads as two systems bolted together. These pin the places Django's wording
would otherwise leak through.
"""

from __future__ import annotations

import re

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
        reverse("account-password"),
        {"old_password": old, "new_password1": new, "new_password2": new},
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
        reverse("account-password"),
        {
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


# --- The register, not just the vocabulary ------------------------------------
# Rules taken from nine live Iranian shops plus Divar's and Digikala's own strings
# (docs-local/archive/persian-copy-research-2026-10-13.md). Each banned word is the
# tell of a translated interface rather than a written one.

# Patterns, not substrings. The ban is on «می‌گردد» as the bureaucratic
# stand-in for «می‌شود» («پرداخت انجام می‌گردد») — but it is a substring of
# «برمی‌گردد», which is a different verb entirely and the plainest way a shop
# can say that money comes back. A guard that forbids the clearest sentence in
# the refund promise is a guard aimed at the wrong thing, so this one looks
# behind it. `test_the_translation_guard_still_catches_what_it_is_for` below
# is the positive control: loosening a check without one is how it quietly
# stops working.
BANNED = [
    r"می‌باشد",  # dead weight; است/هستند does the work
    r"میباشد",
    r"(?<!بر)می‌گردد",  # «انجام می‌گردد» yes, «برمی‌گردد» no
    r"گردید",
    r"نمایید",
    r"سفارشات",  # Arabic broken plural; this shop says سفارش‌ها
    r"پیشنهادات",
    r"گذرواژه",  # Django's word; this shop says رمز عبور
    r"تایید",  # one spelling only: تأیید
    r"&quot;",  # ASCII quotes around Persian; the shop uses «»
]

SHOP_PAGES = ["home", "search", "login", "login-code"]


@pytest.mark.parametrize("name", SHOP_PAGES)
@pytest.mark.parametrize("word", BANNED)
def test_no_shop_page_reads_as_a_translation(client, name, word):
    body = client.get(reverse(name)).content.decode()

    assert not re.search(word, body), word


def test_the_signed_in_pages_read_the_same_way(client, user):
    client.force_login(user)
    body = client.get(reverse("account")).content.decode()

    for word in BANNED:
        assert not re.search(word, body), word


@pytest.mark.parametrize(
    ("text", "caught"),
    [
        ("پرداخت انجام می‌گردد", True),
        ("ارسال می‌گردد و تأیید گردید", True),
        ("اگر سفارش تحویل نشود، وجه برمی‌گردد", False),
        ("مبلغ به کارت شما برمی‌گردد", False),
    ],
)
def test_the_translation_guard_still_catches_what_it_is_for(text, caught):
    """The «می‌گردد» pattern has a lookbehind in it; this is what proves the
    lookbehind did not simply switch the rule off."""
    assert any(re.search(word, text) for word in BANNED) is caught

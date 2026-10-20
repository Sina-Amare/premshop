"""Every ARIA reference on the sign-in and account pages points at something.

Django 5.2 gives a field aria-describedby="<id>_helptext" when it has a hint and
"<id>_error" when it is invalid, trusting the template to print elements with
those ids. A reference to nothing is silence: a screen reader reads neither the
hint nor the error when the field is focused.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.test_auth import PASSWORD

pytestmark = pytest.mark.django_db

REF = re.compile(r'aria-(?:describedby|labelledby)="([^"]*)"')
ID = re.compile(r'\sid="([^"]+)"')


def pages(client) -> dict[str, str]:
    """Each form valid and invalid, as a customer meets it."""
    login, code, account, password = (
        reverse(name) for name in ("login", "login-code", "account", "account-password")
    )
    seen = {
        "sign-in": client.get(login),
        "sign-in, empty": client.post(login, {"email": "", "password": ""}),
        "code request": client.get(code),
        "code request, bad address": client.post(code, {"email": "nope"}),
    }
    client.post(code, {"email": "refs@example.test"})
    seen["code entry, too short"] = client.post(reverse("login-code-verify"), {"code": "12"})

    customer = User.objects.create_user("customer@example.test", PASSWORD, is_verified=True)
    client.force_login(customer)
    seen["account"] = client.get(account)
    seen["account, bad phone"] = client.post(account, {"phone": "abc"})
    seen["password"] = client.get(password)
    seen["password, every error"] = client.post(
        password, {"old_password": "nope", "new_password1": "Ab1!", "new_password2": "Ab1"}
    )
    client.force_login(User.objects.create_user("codeonly@example.test"))
    seen["first password, too short"] = client.post(
        password, {"new_password1": "Ab1!", "new_password2": "Ab1!"}
    )
    return {name: response.content.decode() for name, response in seen.items()}


def test_no_aria_reference_points_at_nothing(client):
    found = pages(client)
    refs = {name: [r for v in REF.findall(body) for r in v.split()] for name, body in found.items()}
    dangling = {
        name: missing
        for name, body in found.items()
        if (missing := sorted(set(refs[name]) - set(ID.findall(body))))
    }
    everything = [r for found_refs in refs.values() for r in found_refs]
    # A scan that saw no reference, or never saw an error, would pass on nothing.
    assert any(r.endswith("_helptext") for r in everything), everything
    assert any(r.endswith("_error") for r in everything), everything
    assert not dangling, dangling

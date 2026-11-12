"""The sign-in pages carry no inline script, style element, style attribute or event
handler, so a content security policy can refuse all four without breaking sign-in."""

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

# Read on the whole page, head included, so a `head_extra` block or a
# `<noscript><style>` is seen as well as the body.
INLINE = {
    "inline script": r"<script\b(?![^>]*\bsrc=)[^>]*>",
    "style element": r"<style\b",
    "style attribute": r"<[^>]+\sstyle=",
    "event handler": r"<[^>]+\son[a-z]+=",
}


@pytest.mark.parametrize("name", ["login-code", "login-code-verify"])
def test_the_sign_in_pages_have_no_inline_code(client, name):
    if name == "login-code-verify":
        client.post(reverse("login-code"), {"email": "markup@example.test"})

    body = client.get(reverse(name)).content.decode()

    assert 'class="card auth-card"' in body, "positive control: the sign-in card rendered"
    found = {kind: re.findall(pattern, body, re.I) for kind, pattern in INLINE.items()}
    assert {kind: hits for kind, hits in found.items() if hits} == {}


def test_the_code_page_loads_its_script_from_a_file(client):
    client.post(reverse("login-code"), {"email": "markup@example.test"})

    body = client.get(reverse("login-code-verify")).content.decode()

    assert re.search(r'<script src="/static/js/login-code\.js" defer>', body)

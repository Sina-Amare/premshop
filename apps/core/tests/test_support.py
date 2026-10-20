"""Where to reach a person is defined once and read everywhere.

The Telegram handle was typed into five templates and the hours into three.
A shop that sends a buyer to a stale handle sends them to a stranger, so
the details live in `apps.core.support` and no template spells them out.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.urls import reverse

from apps.core import support
from apps.core.email import render_email

pytestmark = pytest.mark.django_db

TEMPLATES = Path(settings.BASE_DIR) / "templates"


def test_no_template_spells_out_a_support_detail():
    details = support.details()
    literals = {
        details["telegram"],
        details["telegram_url"].split("//", 1)[1],
        details["email"],
        details["hours_range"],
    }
    found = [
        f"{path.relative_to(TEMPLATES).as_posix()}: {literal}"
        for path in TEMPLATES.rglob("*")
        if path.is_file()
        for literal in literals
        if literal in path.read_text(encoding="utf-8")
    ]
    assert not found


def test_the_scan_would_see_a_typed_handle():
    """The control: the literal is what a template would contain."""
    assert support.details()["telegram"] == "@premshop"
    assert "@premshop" in '<bdi class="latin">@premshop</bdi>'


@pytest.mark.parametrize("url", ["/", "/faq/", "/search/?q=x", "/no-such-page/"])
def test_every_page_reaches_the_same_person(client, url):
    details = support.details()

    body = client.get(url).content.decode()

    assert f'href="{details["telegram_url"]}"' in body, "the header's support link"
    assert details["telegram"] in body, "the footer's handle"
    assert details["hours"] in body
    # Any other hours range means a second literal crept back in. A range
    # followed by «ساعت» is a delivery window, which is a different fact.
    ranges = set(re.findall(r"[۰-۹]+ تا [۰-۹]+(?![۰-۹])(?! ساعت)", body))
    assert ranges <= {details["hours_range"]}, ranges


def test_the_support_link_has_a_name_at_every_width(client):
    """On a phone the label is hidden and the icon is aria-hidden, which left
    the one way to reach a person with no name to a screen reader."""
    body = client.get(reverse("home")).content.decode()

    link = re.search(r'<a href="https://t\.me/[^"]+" class="hlink"[^>]*>(.*?)</a>', body, re.S)
    assert link, "the header's support link"
    # Below 761px the words are visually hidden, never removed, so the name stays.
    assert '<span class="hlink__label">پشتیبانی</span>' in link.group(1)


def test_every_email_gets_the_same_details():
    captured = {}

    def capture(name, context):
        captured.update(context)
        return "x"

    with mock.patch("apps.core.email.render_to_string", side_effect=capture):
        render_email("otp_code", {"code": "123456"})

    assert captured["support"] == support.details()
    assert captured["support"]["email"] == settings.SUPPORT_EMAIL

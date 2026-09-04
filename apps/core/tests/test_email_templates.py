"""Tests for the email templates and the send helper.

Email is the login and delivery channel, so the failures worth guarding are the
quiet ones: a template that stops compiling, an HTML part that silently goes
missing (leaving customers a bare text mail), a newline reaching a header, and a
development-only preview route staying reachable in production.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core import mail

from apps.core.email import render_email, send_templated_email
from apps.core.views import EMAIL_PREVIEWS


@pytest.mark.parametrize("name", sorted(EMAIL_PREVIEWS))
def test_every_template_renders_all_three_parts(name):
    """A typo in any template is a broken send; CI catches it here instead."""
    subject, text, html = render_email(name, EMAIL_PREVIEWS[name])

    assert subject.strip()
    assert text.strip()
    assert html.lstrip().startswith("<!doctype html>")
    assert 'dir="rtl"' in html


@pytest.mark.parametrize("name", sorted(EMAIL_PREVIEWS))
def test_no_template_leaks_an_unrendered_placeholder(name):
    """A misspelled context key renders as empty, not as an error. Catch the shape."""
    _, text, html = render_email(name, EMAIL_PREVIEWS[name])

    for part in (text, html):
        for token in ("{{", "}}", "{%", "%}", "{#", "#}"):
            assert token not in part, f"{token} survived rendering in {name}"


def test_the_message_is_multipart_with_both_halves():
    """HTML-only mail scores worse with spam filters and is unreadable to some clients."""
    sent = send_templated_email(
        "otp_code", to=["customer@example.test"], context=EMAIL_PREVIEWS["otp_code"]
    )

    assert sent == 1
    message = mail.outbox[0]
    assert message.body.strip(), "the plain-text part must not be empty"
    assert len(message.alternatives) == 1
    html, mimetype = message.alternatives[0]
    assert mimetype == "text/html"
    assert "<!doctype html>" in html.lower()


def test_the_code_reaches_both_halves_in_persian_digits():
    """A customer reading either part must see the same code they have to type."""
    _, text, html = render_email("otp_code", EMAIL_PREVIEWS["otp_code"])

    assert "۴۱۸۳۰۵" in text
    assert "۴۱۸۳۰۵" in html
    assert "418305" not in text


def test_replies_go_to_the_support_mailbox():
    send_templated_email(
        "otp_code", to=["customer@example.test"], context=EMAIL_PREVIEWS["otp_code"]
    )

    assert mail.outbox[0].reply_to == [settings.SUPPORT_EMAIL]


def test_a_newline_in_the_context_cannot_inject_a_header():
    """The classic header-injection vector: everything after a raw newline in a
    header is parsed as a new header. The subject is normalised before it can."""
    poisoned = {**EMAIL_PREVIEWS["otp_code"], "code": "111111\nBcc: attacker@example.test"}

    subject, _, _ = render_email("otp_code", poisoned)

    assert "\n" not in subject
    assert "\r" not in subject
    assert "Bcc:" in subject, "the text survives — flattened onto one line, not executed"


def test_order_delivery_never_carries_the_credentials_themselves():
    """ADR-0008: the link is the sanctioned convenience; values never travel by email."""
    _, text, html = render_email("item_delivered", EMAIL_PREVIEWS["item_delivered"])

    for part in (text, html):
        assert "password" not in part.lower()
        assert "رمز" not in part


@pytest.mark.parametrize("name", sorted(EMAIL_PREVIEWS))
def test_the_preview_route_is_unreachable_in_production(client, settings, name):
    settings.DEBUG = False

    assert client.get(f"/dev/emails/{name}/").status_code == 404


@pytest.mark.parametrize("name", sorted(EMAIL_PREVIEWS))
def test_the_preview_route_renders_in_development(client, settings, name):
    settings.DEBUG = True

    response = client.get(f"/dev/emails/{name}/")

    assert response.status_code == 200
    assert b"<!doctype html>" in response.content.lower()


def test_no_template_anywhere_uses_a_multiline_hash_comment():
    """Django's lexer compiles {# ... #} WITHOUT re.DOTALL.

    A comment that crosses a newline is therefore never recognised as one, and is
    emitted as literal text. Regression: this shipped in the site's base template
    from S1 and in the first email layout, putting an English implementation note
    into the page source and into a customer's inbox. The guard covers every
    template in the project, not just email, because that is where it bit.
    """
    import re
    from pathlib import Path

    templates = Path(settings.BASE_DIR, "templates")
    offenders = [
        f"{path.relative_to(templates)}: {match.group(0)[:60]!r}"
        for path in templates.rglob("*.html")
        for match in re.finditer(r"\{#.*?#\}", path.read_text(encoding="utf-8"), re.S)
        if "\n" in match.group(0)
    ]

    assert not offenders, "use {% comment %} for multi-line notes: " + " | ".join(offenders)


@pytest.mark.parametrize("name", sorted(EMAIL_PREVIEWS))
def test_every_font_stack_survives_gmail_dropping_the_web_font(name):
    """Gmail supports no @font-face at all — web, Android or iOS (caniemail.com).

    Vazirmatn is linked for the clients that DO honour it (Apple Mail, Outlook.com,
    Samsung Mail), so every declaration must still name a platform face after it.
    Tahoma may appear only as the last resort: leading with it is what produced the
    first rejected draft, and a stack is decided by what comes first, not by what
    is present.
    """
    import re

    _, _, html = render_email(name, EMAIL_PREVIEWS[name])
    stacks = re.findall(r"font-family:([^;]+);", html)

    assert stacks, "no font-family declaration found at all"
    for stack in stacks:
        faces = [face.strip().strip("'\"") for face in stack.split(",")]
        assert "Vazirmatn" in faces, f"the brand face must be preferred: {stack}"
        assert faces[-1] in {"sans-serif", "serif"}, f"no generic family at the end: {stack}"
        assert len(faces) >= 5, f"too few fallbacks to cover every platform: {stack}"
        if "Tahoma" in faces:
            assert faces.index("Tahoma") >= len(faces) - 3, f"Tahoma too early: {stack}"


@pytest.mark.parametrize("name", sorted(EMAIL_PREVIEWS))
def test_tracking_is_zero_everywhere_except_the_isolated_digits(name):
    """Arabic is cursive: tracking breaks the joins, so letter-spacing is banned.

    Persian digits ۰-۹ never join, so the rule's reason does not reach them and the
    OTP code carries 3px. That is the only exception, and this pins it — a stray
    letter-spacing on Persian prose is invisible in review and wrong on screen.
    """
    import re

    _, _, html = render_email(name, EMAIL_PREVIEWS[name])
    spaced = re.findall(r'letter-spacing:\s*([^;"\']+)', html)

    for value in spaced:
        assert value.strip() in {"0", "3px"}, f"unexpected tracking on Persian text: {value}"
    if name == "otp_code":
        assert "3px" in spaced, "the code lost its tracking"


@pytest.mark.parametrize("name", sorted(EMAIL_PREVIEWS))
def test_the_stack_names_the_persian_faces_iranian_machines_actually_carry(name):
    """Regression: the stack asked only for 'Vazirmatn'.

    An audit of the owner's Windows machine found Vazir.ttf and six weights of
    IRANSans installed — and the family name inside Vazir.ttf is 'Vazir', because
    the project was renamed and older installs register the old name. A family
    name that matches nothing does not error, it falls through in silence, so two
    rounds of "make it softer" landed on Segoe UI and changed nothing on screen.
    """
    import re

    _, _, html = render_email(name, EMAIL_PREVIEWS[name])

    for stack in re.findall(r"font-family:([^;]+);", html):
        faces = [face.strip().strip("'\"") for face in stack.split(",")]
        for required in ("Vazirmatn", "Vazir", "IRANSans"):
            assert required in faces, f"{required} missing from: {stack}"
        assert faces.index("Vazir") < faces.index(
            "Segoe UI"
        ), "a Persian face must outrank Segoe UI"


def test_the_delivery_message_carries_the_logged_in_fallback_link():
    """ADR-0008 constraint 6, and the ADR says omitting any constraint reopens it.

    The magic link is a bearer capability with a 72-hour life. A customer who opens
    the mail on a phone days later, or who has already spent the single use, needs a
    route that does not depend on the token — otherwise the message is a dead end on
    the one thing they paid for.
    """
    _, text, html = render_email("item_delivered", EMAIL_PREVIEWS["item_delivered"])

    for part in (text, html):
        assert EMAIL_PREVIEWS["item_delivered"]["delivery_url"] in part
        assert EMAIL_PREVIEWS["item_delivered"]["order_url"] in part


def test_the_delivery_link_lifetime_matches_the_adr():
    """ADR-0008 settles 72 hours. The first draft invented 48 and nobody would have
    noticed until a customer was told the wrong number."""
    assert EMAIL_PREVIEWS["item_delivered"]["link_ttl_hours"] == 72

    _, text, html = render_email("item_delivered", EMAIL_PREVIEWS["item_delivered"])
    for part in (text, html):
        assert "۷۲" in part

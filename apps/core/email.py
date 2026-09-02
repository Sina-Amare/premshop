"""Rendering and sending templated email.

Every message goes out as **multipart/alternative**: a plain-text part and an HTML
part carrying the same content, with the client choosing. This is not politeness —
HTML-only mail scores worse with spam filters, and the text part is what a screen
reader, a watch, and a stripped-down client actually show.

Each message is three template files, so every Persian string lives in a template
and none in Python:

    templates/email/<name>.subject.txt   one line
    templates/email/<name>.txt           the plain-text part
    templates/email/<name>.html          the HTML part
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def render_email(name: str, context: dict[str, Any]) -> tuple[str, str, str]:
    """Render one message's three parts. Returns (subject, text, html)."""
    full_context: dict[str, Any] = {"support_email": settings.SUPPORT_EMAIL, **context}

    # Collapse ALL whitespace in the subject, not just the trailing newline. A raw
    # newline in a header is the classic header-injection vector: everything after
    # it is parsed as a new header. Django would raise, but a subject is assembled
    # from context, so the value is normalised before it can ever get there.
    subject = " ".join(render_to_string(f"email/{name}.subject.txt", full_context).split())

    text = render_to_string(f"email/{name}.txt", full_context).strip() + "\n"
    html = render_to_string(f"email/{name}.html", full_context)
    return subject, text, html


def send_templated_email(name: str, *, to: list[str], context: dict[str, Any]) -> int:
    """Render and send. Returns the number of messages the backend accepted.

    Reply-To is set explicitly: the From address is a monitored mailbox, but making
    the reply target explicit means a customer's reply still arrives if the From
    display name is ever changed to something unmonitored.
    """
    subject, text, html = render_email(name, context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        to=to,
        reply_to=[settings.SUPPORT_EMAIL],
    )
    message.attach_alternative(html, "text/html")
    return message.send()

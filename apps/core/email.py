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

from smtplib import SMTPNotSupportedError, SMTPRecipientsRefused, SMTPResponseException
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template import Context, Engine
from django.template.loader import render_to_string

from apps.core import email_log, support


def _render_plain(template_name: str, context: dict[str, Any]) -> str:
    # Escaping is for HTML: in a subject or a text part, `&amp;` is what the reader
    # sees, and it breaks any link with a query string.
    template = Engine.get_default().get_template(template_name)
    return template.render(Context(context, autoescape=False))


def render_email(name: str, context: dict[str, Any]) -> tuple[str, str, str]:
    """Render one message's three parts. Returns (subject, text, html)."""
    full_context: dict[str, Any] = {"support": support.details(), **context}

    # Collapse ALL whitespace in the subject, not just the trailing newline. A raw
    # newline in a header is the classic header-injection vector: everything after
    # it is parsed as a new header. Django would raise, but a subject is assembled
    # from context, so the value is normalised before it can ever get there.
    subject = " ".join(_render_plain(f"email/{name}.subject.txt", full_context).split())

    text = _render_plain(f"email/{name}.txt", full_context).strip() + "\n"
    html = render_to_string(f"email/{name}.html", full_context)
    return subject, text, html


def send_templated_email(
    name: str,
    *,
    to: list[str],
    context: dict[str, Any],
    rendered_parts: tuple[str, str, str] | None = None,
) -> int:
    """Render and send. Returns the number of messages the backend accepted.

    Reply-To is set explicitly: the From address is a monitored mailbox, but making
    the reply target explicit means a customer's reply still arrives if the From
    display name is ever changed to something unmonitored.
    """
    subject, text, html = (
        rendered_parts if rendered_parts is not None else render_email(name, context)
    )
    subject = " ".join(subject.split())
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        to=to,
        reply_to=[settings.SUPPORT_EMAIL],
    )
    message.attach_alternative(html, "text/html")
    if not to:
        return 0
    try:
        attempts = email_log.reserve(name, to)
    except email_log.MailQuotaExceeded:
        raise
    except Exception as error:  # noqa: BLE001 — accounting must not disable security mail
        email_log.accounting_error(error)
        if name not in email_log.PROTECTED:
            raise email_log.MailQuotaExceeded from None
        attempts = []

    def finish(status: str, error: Exception | None = None) -> None:
        try:
            email_log.finish(attempts, status, error)
        except Exception as failure:  # noqa: BLE001 — an accepted code must remain successful
            email_log.accounting_error(failure)

    try:
        backend = get_connection()
        backend.open()
    except Exception as error:
        finish("rejected", error)
        raise
    try:
        message.connection = backend
        try:
            sent = message.send()
        except (SMTPResponseException, SMTPRecipientsRefused, SMTPNotSupportedError) as error:
            finish("rejected", error)
            raise
        except Exception as error:
            finish("uncertain", error)
            raise
        finish("accepted" if sent else "rejected")
        return sent
    finally:
        try:
            backend.close()
        except Exception as error:  # noqa: BLE001 — closing cannot change the send result
            email_log.accounting_error(error)

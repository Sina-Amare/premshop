"""Sign-in codes by SMS, through SMS.ir.

One attempt per request, as a code email was sent: the customer's resend is the
retry, since a code that arrives minutes late is no use. A failure keeps only the
HTTP status and SMS.ir's own status number, because a reply's text can repeat the
number and the code. The API is in Iran, so it is called directly, never through a
proxy the machine may hold for Telegram.

SMS_IR_TEMPLATE_ID set: SMS.ir's `verify` call with the shop's approved template,
whose one variable is `#CODE#`. Unset: a plain message from SMS_IR_LINE_NUMBER,
which needs no template and serves until the template is approved.
"""

from __future__ import annotations

import json
from http.client import HTTPSConnection
from typing import Any

from django.conf import settings
from django.template.loader import render_to_string

HOST = "api.sms.ir"
TIMEOUT = 10
# The fake backend's record, for tests: (mobile, text) per send.
outbox: list[tuple[str, str]] = []


class SmsNotSent(Exception):
    def __init__(self, status: int | None, provider_status: int | None = None) -> None:
        super().__init__(f"SMS not sent (HTTP {status or 'network'}, SMS.ir {provider_status})")
        self.status = status
        self.provider_status = provider_status


def send_code(mobile: str, code: str) -> None:
    """Send a sign-in code to an `09…` number, or raise SmsNotSent."""
    text = render_to_string("sms/login_code.txt", {"code": code}).strip()
    if settings.SMS_BACKEND == "fake":
        outbox.append((mobile, text))
        return
    if not settings.SMS_IR_API_KEY:
        raise SmsNotSent(None)
    if settings.SMS_IR_TEMPLATE_ID:
        _post(
            "/v1/send/verify",
            {
                "mobile": mobile,
                "templateId": int(settings.SMS_IR_TEMPLATE_ID),
                "parameters": [{"name": "CODE", "value": code}],
            },
        )
    else:
        _post(
            "/v1/send/bulk",
            {
                "lineNumber": int(settings.SMS_IR_LINE_NUMBER),
                "messageText": text,
                "mobiles": [mobile],
            },
        )


def _post(path: str, body: dict[str, Any]) -> None:
    connection = HTTPSConnection(HOST, timeout=TIMEOUT)
    try:
        connection.request(
            "POST",
            path,
            body=json.dumps(body).encode(),
            headers={
                "X-API-KEY": settings.SMS_IR_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        raw = response.read()
    except Exception:  # noqa: BLE001 - socket, TLS and http.client errors; some quote the body
        raise SmsNotSent(None) from None
    finally:
        connection.close()
    try:
        status = json.loads(raw).get("status")
    except Exception:  # noqa: BLE001 - a gateway's error page, or a reply of another shape
        raise SmsNotSent(response.status) from None
    if response.status != 200 or status != 1:
        raise SmsNotSent(response.status, status if isinstance(status, int) else None)

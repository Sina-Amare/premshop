"""The SMS sender: SMS.ir's two calls, and every refusal raised without its text."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from apps.core import sms

PHONE, CODE = "09123456789", "418305"
ACCEPTED = (200, {"status": 1, "message": "موفق", "data": {"messageId": 1, "cost": 1}})


class FakeConnection:
    calls: list[tuple[str, str, dict, dict]] = []
    reply: tuple[int, object] = ACCEPTED
    error: Exception | None = None

    def __init__(self, host: str, timeout: float) -> None:
        self.host, self.timeout = host, timeout

    def request(self, method: str, path: str, body: bytes, headers: dict) -> None:
        if FakeConnection.error is not None:
            raise FakeConnection.error
        FakeConnection.calls.append((method, path, json.loads(body), headers))

    def getresponse(self) -> SimpleNamespace:
        status, body = FakeConnection.reply
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return SimpleNamespace(status=status, read=lambda: raw)

    def close(self) -> None:
        pass


@pytest.fixture
def smsir(settings, monkeypatch):
    settings.SMS_BACKEND = "smsir"
    settings.SMS_IR_API_KEY = "test-key"
    settings.SMS_IR_TEMPLATE_ID = ""
    settings.SMS_IR_LINE_NUMBER = "500032685089846"
    FakeConnection.calls, FakeConnection.reply, FakeConnection.error = [], ACCEPTED, None
    monkeypatch.setattr(sms, "HTTPSConnection", FakeConnection)
    return FakeConnection


def test_a_plain_message_goes_from_the_shops_line(smsir):
    sms.send_code(PHONE, CODE)

    method, path, body, headers = smsir.calls[0]
    assert (method, path) == ("POST", "/v1/send/bulk")
    assert body["lineNumber"] == 500032685089846 and body["mobiles"] == [PHONE]
    assert headers["X-API-KEY"] == "test-key"
    # Latin digits, so a phone offers to fill the code in; a Persian word first, so
    # the message lays out right to left.
    assert CODE in body["messageText"] and body["messageText"].startswith("کد ورود")


def test_an_approved_template_takes_the_code_as_its_one_parameter(smsir, settings):
    settings.SMS_IR_TEMPLATE_ID = "123456"

    sms.send_code(PHONE, CODE)

    _, path, body, _ = smsir.calls[0]
    assert path == "/v1/send/verify"
    assert body == {
        "mobile": PHONE,
        "templateId": 123456,
        "parameters": [{"name": "CODE", "value": CODE}],
    }


@pytest.mark.parametrize(
    "reply",
    [
        (401, {"status": 0, "message": f"key refused for {PHONE}"}),
        (500, b"<html>gateway error</html>"),
        (200, {"status": 0, "message": f"invalid number {PHONE} for {CODE}"}),
        (200, b"not json"),
    ],
    ids=["unauthorised", "server-error", "refused", "unreadable"],
)
def test_every_refusal_raises_without_its_text(smsir, reply):
    smsir.reply = reply

    with pytest.raises(sms.SmsNotSent) as raised:
        sms.send_code(PHONE, CODE)

    assert raised.value.status == reply[0]
    assert PHONE not in str(raised.value) and CODE not in str(raised.value)


def test_a_broken_connection_raises_without_its_text(smsir):
    smsir.error = TimeoutError(f"timed out sending {CODE} to {PHONE}")

    with pytest.raises(sms.SmsNotSent) as raised:
        sms.send_code(PHONE, CODE)

    assert raised.value.status is None and raised.value.__suppress_context__
    assert CODE not in str(raised.value)


def test_without_a_key_nothing_is_sent(smsir, settings):
    settings.SMS_IR_API_KEY = ""

    with pytest.raises(sms.SmsNotSent):
        sms.send_code(PHONE, CODE)

    assert not smsir.calls


def test_the_fake_backend_keeps_what_it_would_have_sent(settings):
    settings.SMS_BACKEND = "fake"

    sms.send_code(PHONE, CODE)

    assert sms.outbox[-1][0] == PHONE and CODE in sms.outbox[-1][1]

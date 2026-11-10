import json
import logging
import socket
import threading
from http.client import IncompleteRead, InvalidURL
from types import SimpleNamespace

import pytest

from apps.core import telegram
from apps.core.observability import PrivateLinkFilter, redact_url, scrub_event

TOKEN = "123456789:AAEexampleSECRETexampleSECRETexample12"  # noqa: S105  # nosec B105
SECRET = TOKEN.split(":")[1]
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
SENT = {"ok": True, "result": {"message_id": 7}}


@pytest.fixture(autouse=True)
def token(settings):
    settings.TELEGRAM_BOT_TOKEN = TOKEN


@pytest.fixture
def api(monkeypatch):
    """Telegram's side: each request takes the next scripted outcome, by default a sent message."""
    fake = SimpleNamespace(outcomes=[], requests=[], connections=[], now=1000.0)

    class Response:
        def __init__(self, status, body):
            self.status, self.body = status, body

        def read(self):
            if isinstance(self.body, Exception):
                raise self.body
            return self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()

    class Connection:
        def __init__(self, host, port=None, timeout=None):
            self.address, self.tunnel = (host, port), None
            self.timeout, self.sock, self.closed = timeout, None, False
            fake.connections.append(self)

        def set_tunnel(self, host, port=None):
            self.tunnel = (host, port)

        def request(self, method, path, body, headers):
            outcome = fake.outcomes.pop(0) if fake.outcomes else (200, SENT)
            if isinstance(outcome, Exception):
                raise outcome
            fake.requests.append((method, path, json.loads(body), self.timeout))
            self.response = Response(*outcome)

        def getresponse(self):
            return self.response

        def close(self):
            self.closed = True

    monkeypatch.setattr(telegram, "HTTPSConnection", Connection)
    monkeypatch.setattr(telegram, "getproxies", lambda: {})
    monkeypatch.setattr(telegram, "_local", threading.local())
    monkeypatch.setattr(telegram, "time", SimpleNamespace(monotonic=lambda: fake.now))
    return fake


def test_a_message_goes_out_as_escaped_html_without_a_preview(api):
    assert telegram.send_message(42, "سفارش ۱۰۲۴") == {"message_id": 7}
    method, path, data, timeout = api.requests[0]
    assert (method, path, timeout) == ("POST", f"/bot{TOKEN}/sendMessage", 5)
    assert (api.connections[0].address, api.connections[0].tunnel) == (
        ("api.telegram.org", None),
        None,
    )
    assert data == {
        "chat_id": 42,
        "text": "سفارش ۱۰۲۴",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }


@pytest.mark.parametrize(
    ("outcome", "status", "retry_after"),
    [
        ((429, {"ok": False, "parameters": {"retry_after": 7}}), 429, 7),
        ((403, {"ok": False, "description": "Forbidden: bot was blocked"}), 403, None),
        ((502, b""), 502, None),
        (ConnectionRefusedError(f"no route to {URL}"), None, None),
        (TimeoutError(f"timed out on {URL}"), None, None),
        # A relay that closes mid-body, and a token with a space, whose error quotes the URL.
        ((200, IncompleteRead(b"partial")), None, None),
        (InvalidURL(f"URL can't contain control characters. {URL!r}"), None, None),
    ],
)
def test_every_failure_becomes_a_clean_error_without_the_token(api, outcome, status, retry_after):
    api.outcomes.append(outcome)
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send_message(42, "x")
    assert (caught.value.status, caught.value.retry_after) == (status, retry_after)
    # Raised from None: no chained exception carries the URL and its token.
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert SECRET not in str(caught.value) and SECRET not in repr(caught.value)


def test_a_refusal_whose_body_breaks_off_is_still_a_clean_error(api):
    api.outcomes.append((429, IncompleteRead(b"{")))
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send_message(42, "x")
    assert (caught.value.status, caught.value.retry_after) == (429, None)


@pytest.mark.parametrize("body", [b"<html>proxy error</html>", b'["not", "an", "object"]'])
def test_an_unreadable_reply_is_a_failure(api, body):
    api.outcomes.append((200, body))
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send_message(42, "x")
    assert caught.value.status is None


def test_one_connection_serves_call_after_call(api):
    # Through a VPN or the relay, a new encrypted connection costs most of a second.
    for _ in range(3):
        telegram.send_message(42, "x")
    assert len(api.connections) == 1


def test_a_connection_that_failed_is_never_reused(api):
    api.outcomes.append(TimeoutError())
    with pytest.raises(telegram.TelegramError):
        telegram.send_message(42, "x")
    telegram.send_message(42, "x")
    assert len(api.connections) == 2 and api.connections[0].closed


@pytest.mark.parametrize("status", [200, 429])
def test_a_reply_cut_off_mid_body_closes_its_connection(api, status):
    api.outcomes.append((status, IncompleteRead(b'{"ok": tr')))
    with pytest.raises(telegram.TelegramError):
        telegram.send_message(42, "x")
    telegram.send_message(42, "x")
    # The rest of a cut-off reply must never be read as the next request's answer.
    assert len(api.connections) == 2 and api.connections[0].closed


def test_a_connection_idle_too_long_is_replaced(api):
    telegram.send_message(42, "x")
    api.now += telegram.IDLE_SECONDS + 1
    telegram.send_message(42, "x")
    assert len(api.connections) == 2 and api.connections[0].closed


def test_a_connection_the_server_closed_is_replaced(api):
    telegram.send_message(42, "x")
    ours, theirs = socket.socketpair()
    api.connections[0].sock = ours
    try:
        theirs.close()  # the server hangs up on an idle connection
        telegram.send_message(42, "x")
    finally:
        ours.close()
    assert len(api.connections) == 2 and api.connections[0].closed


def test_a_system_proxy_carries_the_connection_as_urllib_did(api, monkeypatch):
    # A VPN in proxy mode, or a relay set up as one: without it Telegram is out of reach.
    monkeypatch.setattr(telegram, "getproxies", lambda: {"https": "http://127.0.0.1:10809"})
    monkeypatch.setattr(telegram, "proxy_bypass", lambda host: False)
    telegram.send_message(42, "x")
    connection = api.connections[0]
    assert (connection.address, connection.tunnel) == (
        ("127.0.0.1", 10809),
        ("api.telegram.org", 443),
    )


def test_the_bot_token_is_scrubbed_from_logs_and_error_reports():
    assert SECRET not in redact_url(URL)
    event = {
        "message": f"send failed: {URL}",
        "exception": {"values": [{"type": "URLError", "value": f"<urlopen error {URL}>"}]},
        # Sentry's HTTP integration records every outgoing URL as a breadcrumb.
        "breadcrumbs": {"values": [{"type": "http", "data": {"url": URL, "method": "POST"}}]},
    }
    assert SECRET not in json.dumps(scrub_event(event))  # type: ignore[arg-type]
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "send failed: %s", (URL,), None)
    assert SECRET not in PrivateLinkFilter().filter(record).getMessage()

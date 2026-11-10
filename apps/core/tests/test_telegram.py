import io
import json
import logging
from http.client import IncompleteRead, InvalidURL
from urllib.error import HTTPError, URLError

import pytest

from apps.core import telegram
from apps.core.observability import PrivateLinkFilter, redact_url, scrub_event

TOKEN = "123456789:AAEexampleSECRETexampleSECRETexample12"  # noqa: S105  # nosec B105
SECRET = TOKEN.split(":")[1]
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


@pytest.fixture(autouse=True)
def token(settings):
    settings.TELEGRAM_BOT_TOKEN = TOKEN


class Reply:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return io.BytesIO(self.body)

    def __exit__(self, *exc):
        return False


def refused(code, body):
    return HTTPError(URL, code, "refused", {}, io.BytesIO(json.dumps(body).encode()))  # type: ignore[arg-type]


def test_a_message_goes_out_as_escaped_html_without_a_preview(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen.update(url=request.full_url, data=json.loads(request.data), timeout=timeout)
        return Reply({"ok": True, "result": {"message_id": 7}})

    monkeypatch.setattr(telegram, "urlopen", urlopen)
    assert telegram.send_message(42, "سفارش ۱۰۲۴") == {"message_id": 7}
    assert seen["url"].endswith("/sendMessage")
    assert seen["data"] == {
        "chat_id": 42,
        "text": "سفارش ۱۰۲۴",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    assert seen["timeout"] == 5


@pytest.mark.parametrize(
    ("error", "status", "retry_after"),
    [
        (refused(429, {"ok": False, "parameters": {"retry_after": 7}}), 429, 7),
        (refused(403, {"ok": False, "description": "Forbidden: bot was blocked"}), 403, None),
        (refused(502, {}), 502, None),
        (URLError(f"no route to {URL}"), None, None),
        (TimeoutError(f"timed out on {URL}"), None, None),
        # A relay that closes mid-body, and a token with a space, whose error quotes the URL.
        (IncompleteRead(b"partial"), None, None),
        (InvalidURL(f"URL can't contain control characters. {URL!r}"), None, None),
    ],
)
def test_every_failure_becomes_a_clean_error_without_the_token(
    monkeypatch, error, status, retry_after
):
    def urlopen(request, timeout):
        raise error

    monkeypatch.setattr(telegram, "urlopen", urlopen)
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send_message(42, "x")
    assert (caught.value.status, caught.value.retry_after) == (status, retry_after)
    # Raised from None: no chained exception carries the URL and its token.
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert SECRET not in str(caught.value) and SECRET not in repr(caught.value)


def test_a_refusal_whose_body_breaks_off_is_still_a_clean_error(monkeypatch):
    class Truncated(io.BytesIO):
        def read(self, *args):
            raise IncompleteRead(b"{")

    def urlopen(request, timeout):
        raise HTTPError(URL, 429, "refused", {}, Truncated())  # type: ignore[arg-type]

    monkeypatch.setattr(telegram, "urlopen", urlopen)
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send_message(42, "x")
    assert (caught.value.status, caught.value.retry_after) == (429, None)


@pytest.mark.parametrize("body", [b"<html>proxy error</html>", b'["not", "an", "object"]'])
def test_an_unreadable_reply_is_a_failure(monkeypatch, body):
    class Garbage(Reply):
        def __enter__(self):
            return io.BytesIO(body)

    monkeypatch.setattr(telegram, "urlopen", lambda request, timeout: Garbage({}))
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send_message(42, "x")
    assert caught.value.status is None


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

"""The Telegram Bot API over plain HTTPS, for every app that talks to the shop's bot.

The bot token is part of every request's path, and a network error's text, a
traceback and an error tracker's breadcrumbs all carry it. So every failure is
re-raised as TelegramError from None, keeping only the HTTP status and Telegram's
retry_after; `observability.redact_url` scrubs any token that gets out some other way.

Each thread keeps one connection open: through a VPN or the relay abroad, a new
encrypted connection costs most of a second, and the bot's replies should not.
"""

from __future__ import annotations

import json
import select
import threading
import time
from http.client import HTTPSConnection
from typing import Any

from django.conf import settings

HOST = "api.telegram.org"
# Longer idle, and the connection may have been dropped on the way without a word.
IDLE_SECONDS = 30
_local = threading.local()


class TelegramError(Exception):
    def __init__(self, status: int | None, retry_after: int | None = None) -> None:
        super().__init__(f"Telegram request failed ({status or 'network'})")
        self.status = status
        self.retry_after = retry_after


def _retry_after(body: bytes) -> int | None:
    try:
        return int(json.loads(body)["parameters"]["retry_after"])
    except Exception:  # noqa: BLE001 - any body that is not Telegram's refusal
        return None


def _drop() -> None:
    connection, _local.connection = getattr(_local, "connection", None), None
    if connection is not None:
        connection.close()


def _connection(timeout: float) -> HTTPSConnection:
    connection = getattr(_local, "connection", None)
    if connection is not None:
        sock = connection.sock
        # An idle socket that turns readable has been closed by the server.
        if time.monotonic() - _local.used > IDLE_SECONDS or (
            sock is not None and select.select([sock], [], [], 0)[0]
        ):
            _drop()
            connection = None
    if connection is None:
        connection = _local.connection = HTTPSConnection(HOST, timeout=timeout)
    connection.timeout = timeout
    if connection.sock is not None:
        connection.sock.settimeout(timeout)
    return connection


def call(method: str, payload: dict[str, Any], *, timeout: float = 5) -> Any:
    try:
        connection = _connection(timeout)
        connection.request(
            "POST",
            f"/bot{settings.TELEGRAM_BOT_TOKEN}/{method}",
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
    except Exception:  # noqa: BLE001 - socket, TLS and http.client errors; some quote the path
        _drop()
        raise TelegramError(None) from None
    try:
        body = response.read()
        _local.used = time.monotonic()
    except Exception:  # noqa: BLE001 - a body that breaks off (IncompleteRead) included
        _drop()
        body = b""
    if response.status != 200:
        raise TelegramError(response.status, _retry_after(body)) from None
    try:
        return json.loads(body)["result"]
    except Exception:  # noqa: BLE001 - a relay's error page, or a reply of another shape
        raise TelegramError(None) from None


def _text(text: str) -> dict[str, Any]:
    return {"text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}


def send_message(
    chat_id: int, text: str, *, buttons: list[list[dict[str, str]]] | None = None
) -> Any:
    """`text` is Telegram HTML: the caller escapes every value it puts in."""
    payload = {"chat_id": chat_id, **_text(text)}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return call("sendMessage", payload)


def edit_message(chat_id: int, message_id: int, text: str) -> Any:
    return call("editMessageText", {"chat_id": chat_id, "message_id": message_id, **_text(text)})


def answer_callback(callback_id: str) -> Any:
    return call("answerCallbackQuery", {"callback_query_id": callback_id})


def get_updates(offset: int | None, timeout: int) -> list[dict[str, Any]]:
    """Long polling: Telegram holds the request open up to `timeout` seconds."""
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    return list(call("getUpdates", payload, timeout=timeout + 10))

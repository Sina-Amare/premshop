"""The Telegram Bot API over plain HTTPS, for every app that talks to the shop's bot.

The bot token is part of every request's URL, and a network error's text, a
traceback and an error tracker's breadcrumbs all carry URLs. So every failure is
re-raised as TelegramError from None, keeping only the HTTP status and Telegram's
retry_after; `observability.redact_url` scrubs any token that gets out some other way.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    def __init__(self, status: int | None, retry_after: int | None = None) -> None:
        super().__init__(f"Telegram request failed ({status or 'network'})")
        self.status = status
        self.retry_after = retry_after


def _retry_after(error: HTTPError) -> int | None:
    try:
        return int(json.loads(error.read())["parameters"]["retry_after"])
    except OSError, ValueError, KeyError, TypeError:
        return None


def call(method: str, payload: dict[str, Any], *, timeout: float = 5) -> Any:
    request = Request(  # noqa: S310 - a fixed https address
        API.format(token=settings.TELEGRAM_BOT_TOKEN, method=method),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - as above
            return json.load(response)["result"]
    except HTTPError as error:
        raise TelegramError(error.code, _retry_after(error)) from None
    except OSError, ValueError, KeyError:
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

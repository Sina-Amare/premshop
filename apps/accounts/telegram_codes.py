"""A queued sign-in request belongs to one request and one Telegram link."""

from __future__ import annotations

import logging
import time

from django.core.cache import cache
from django.db import transaction
from django.template.loader import render_to_string

from apps.accounts import otp
from apps.accounts.models import User
from apps.core import telegram

logger = logging.getLogger(__name__)


def pending_key(phone: str) -> str:
    return f"otp-pending:{phone}"


def link_generation(user: User | None) -> otp.TelegramLink | None:
    if user is None:
        return None
    return (
        user.pk, user.telegram_id,
        user.telegram_linked_at.isoformat() if user.telegram_linked_at else None,
    )


def _current(
    user: User | None, phone: str, request_id: str, expires_at: int,
    generation: otp.TelegramLink | None,
) -> bool:
    return bool(
        user and user.is_active and not user.is_staff and user.telegram_id
        and user.telegram_linked_at and link_generation(user) == generation
        and expires_at > int(time.time()) and cache.get(pending_key(phone)) == request_id
    )


def deliver_code(
    phone: str, request_id: str, expires_at: int, generation: otp.TelegramLink | None,
) -> None:
    remaining = expires_at - int(time.time())
    if remaining <= 0 or not cache.add(f"otp-dispatch:{request_id}", True, timeout=remaining):
        return
    with transaction.atomic():
        user = User.objects.select_for_update(no_key=True).filter(phone=phone).first()
        if user is None or user.telegram_id is None or not _current(user, phone, request_id, expires_at, generation):
            return
        chat_id = user.telegram_id
    code = otp._new_code()
    try:
        message = telegram.send_message(
            chat_id,
            render_to_string("telegram/login_code.txt", {"code": code}).strip(),
            protect_content=True,
        )
    except telegram.TelegramError as error:
        logger.error("Telegram code not sent: HTTP %s", error.status or "network")
        return
    with transaction.atomic():
        user = User.objects.select_for_update(no_key=True).filter(phone=phone).first()
        if _current(user, phone, request_id, expires_at, generation):
            otp._store(
                phone, code, expires_at=expires_at, telegram_link=generation,
                telegram_message=(chat_id, int(message["message_id"])),
            )


def delete_message(chat_id: int, message_id: int) -> None:
    try:
        telegram.delete_message(chat_id, message_id)
    except telegram.TelegramError as error:
        logger.error("Telegram code message not deleted: HTTP %s", error.status or "network")

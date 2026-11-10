"""Linking a customer's Telegram, and everything the shop's bot hears.

A link is completed only by a token the site issued to a signed-in account. The bot
never hands out a web link that links on click: a scammer could send a victim one,
and the victim's account would be tied to the scammer's Telegram, which receives
sign-in codes once S8c lands.
"""

from __future__ import annotations

import logging
import secrets
import time
from functools import partial
from html import escape
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core import ratelimit, support, telegram
from apps.core.email import send_templated_email

logger = logging.getLogger(__name__)

LINK_SECONDS = 10 * 60
OFFSET_KEY = "telegram:update-offset"
DOWN_KEY = "telegram:poll-down"

CONFIRM = "اتصال این تلگرام به حساب <code>{email}</code> در پرم‌شاپ را تأیید کنید."
CONFIRM_BUTTON = "تأیید اتصال"
LINKED = "تلگرام شما به حساب پرم‌شاپ وصل شد.\nقطع اتصال: {account}"
ALREADY = "این تلگرام به حساب پرم‌شاپ شما وصل است."
LIMITED = "سقف اتصال این تلگرام برای امروز پر شده است."
EXPIRED = "این پیوند دیگر معتبر نیست.\nدریافت پیوند جدید: {account}"
POINTER = (
    "ربات اطلاع‌رسانی سفارش‌های پرم‌شاپ؛ پیام‌ها اینجا خوانده نمی‌شوند.\n"
    "پشتیبانی: @{support}\n"
    "اتصال حساب: {account}"
)


def _record_key(token: str) -> str:
    return f"telegram-link:{token}"


def _current_key(user_id: int) -> str:
    return f"telegram-link-user:{user_id}"


def _account_url() -> str:
    return settings.SITE_URL + reverse("account")


def start_link(user: User) -> str:
    """Issue the account's one live link token; issuing another replaces it."""
    token = secrets.token_urlsafe(16)
    previous = cache.get(_current_key(user.pk))
    if previous:
        cache.delete(_record_key(previous))
    cache.set(_record_key(token), user.pk, timeout=LINK_SECONDS)
    cache.set(_current_key(user.pk), token, timeout=LINK_SECONDS)
    return token


def deep_link(token: str) -> str:
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"


def link_owner(token: str) -> User | None:
    """The account a live token would link, without spending the token."""
    user_id = cache.get(_record_key(token))
    if user_id is None or cache.get(_current_key(user_id)) != token:
        return None
    return User.objects.filter(pk=user_id, is_active=True).first()


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}•••@{domain}"


def confirm_link(token: str, sender: dict[str, Any]) -> User | None:
    """Spend a live token on the sender's Telegram. None if it is spent or never was."""
    user = link_owner(token)
    # The delete that succeeds is the single use, as in otp.verify.
    if user is None or not cache.delete(_record_key(token)):
        return None
    cache.delete(_current_key(user.pk))
    telegram_id = int(sender["id"])
    username = str(sender.get("username") or "")
    # Never the display name: it is free text, and a scammer can name theirs as the shop.
    handle = f"@{username}" if username else str(telegram_id)
    with transaction.atomic():
        changed = True
        for holder in (
            User.objects.select_for_update(no_key=True)
            .filter(Q(pk=user.pk) | Q(telegram_id=telegram_id))
            .order_by("pk")
        ):
            if holder.pk == user.pk:
                changed = holder.telegram_id != telegram_id
                continue
            # One Telegram, one account: it moves, and its old account is told.
            User.objects.filter(pk=holder.pk).update(
                telegram_id=None, telegram_username="", telegram_linked_at=None
            )
            transaction.on_commit(partial(_notify, holder, "moved", handle))
        User.objects.filter(pk=user.pk).update(
            telegram_id=telegram_id, telegram_username=username, telegram_linked_at=timezone.now()
        )
        # A relink that changes nothing sends nothing: each email draws on the mail budget.
        if changed:
            transaction.on_commit(partial(_notify, user, "linked", handle))
    return user


def unlink(user: User) -> None:
    with transaction.atomic():
        holder = User.objects.select_for_update(no_key=True).get(pk=user.pk)
        if holder.telegram_id is None:
            return
        handle = f"@{holder.telegram_username}" if holder.telegram_username else ""
        User.objects.filter(pk=user.pk).update(
            telegram_id=None, telegram_username="", telegram_linked_at=None
        )
        transaction.on_commit(partial(_notify, holder, "unlinked", handle))


def _notify(user: User, state: str, handle: str) -> None:
    try:
        send_templated_email(
            "telegram_changed",
            to=[user.email],
            context={
                "state": state,
                "telegram": handle,
                "changed_at": timezone.now(),
                "account_url": _account_url(),
            },
        )
    except Exception as error:  # noqa: BLE001 - a notice cannot undo the link
        status = getattr(error, "smtp_code", None)
        logger.error(
            "telegram link notice not sent for user %s: %s%s",
            user.pk,
            type(error).__name__,
            f" (SMTP {status})" if isinstance(status, int) else "",
        )


def _private_chat(message: dict[str, Any]) -> int | None:
    """The chat's id when it is a private chat; groups and channels link nothing."""
    chat = message.get("chat") or {}
    return int(chat["id"]) if chat.get("type") == "private" else None


def handle_update(update: dict[str, Any]) -> None:
    if "callback_query" in update:
        _pressed(update["callback_query"])
    elif "message" in update:
        _messaged(update["message"])


def _messaged(message: dict[str, Any]) -> None:
    chat_id = _private_chat(message)
    if chat_id is None:
        return
    command, _, token = str(message.get("text") or "").partition(" ")
    token = token.strip()
    user = link_owner(token) if command == "/start" and token else None
    if user is not None:
        telegram.send_message(
            chat_id,
            CONFIRM.format(email=escape(mask_email(user.email))),
            buttons=[[{"text": CONFIRM_BUTTON, "callback_data": f"link:{token}"}]],
        )
    elif command == "/start" and token:
        telegram.send_message(chat_id, EXPIRED.format(account=_account_url()))
    else:
        telegram.send_message(
            chat_id, POINTER.format(account=_account_url(), support=support.TELEGRAM_HANDLE)
        )


def _pressed(callback: dict[str, Any]) -> None:
    message = callback.get("message") or {}
    chat_id = _private_chat(message)
    if chat_id is None:
        return
    sender = callback["from"]
    token = str(callback.get("data") or "").removeprefix("link:")
    if link_owner(token) is not None and not ratelimit.hit(
        ratelimit.TELEGRAM_CONFIRMS_PER_TELEGRAM, str(sender["id"])
    ):
        text = LIMITED
    elif confirm_link(token, sender) is not None:
        text = LINKED.format(account=_account_url())
    elif User.objects.filter(telegram_id=sender["id"]).exists():
        text = ALREADY
    else:
        text = EXPIRED.format(account=_account_url())
    telegram.answer_callback(callback["id"])
    telegram.edit_message(chat_id, message["message_id"], text)


def poll(seconds: float = 45) -> int:
    """Fetch and answer the bot's updates for about `seconds`. Returns how many arrived.

    Each update's offset is kept the moment it is handled, so a crash replays at most
    the update in hand. One outage logs one ERROR, which error tracking turns into one
    event, then WARNINGs until Telegram answers again.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        return 0
    deadline = time.monotonic() + seconds
    received = 0
    while True:
        wait = max(0, min(25, int(deadline - time.monotonic())))
        try:
            updates = telegram.get_updates(cache.get(OFFSET_KEY), wait)
        except telegram.TelegramError as error:
            first = cache.add(DOWN_KEY, True, timeout=None)
            logger.log(
                logging.ERROR if first else logging.WARNING,
                "telegram poll failed: TelegramError %s",
                error.status or "network",
            )
            return received
        if cache.delete(DOWN_KEY):
            logger.info("telegram poll recovered")
        for update in updates:
            try:
                handle_update(update)
            except Exception as error:  # noqa: BLE001 - one bad update must not block the rest
                logger.error(
                    "telegram update %s failed: %s", update.get("update_id"), type(error).__name__
                )
            cache.set(OFFSET_KEY, int(update["update_id"]) + 1, timeout=None)
            received += 1
        if time.monotonic() >= deadline:
            return received

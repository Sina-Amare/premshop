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
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from html import escape
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.accounts.models import User
from apps.core import ratelimit, support, telegram
from apps.core import telegram_emoji as emoji
from apps.core.email import send_templated_email
from apps.core.formatting import mask_phone

logger = logging.getLogger(__name__)

LINK_SECONDS = 10 * 60
OFFSET_KEY = "telegram:update-offset"
DOWN_KEY = "telegram:poll-down"
COMMANDS_KEY = "telegram:commands"

# Every screen is a rich message that opens with its emoji and title as a heading, and its
# links are buttons.
CONFIRM = (
    f"<h2>{emoji.LINK} اتصال تلگرام به PremShop</h2>"
    "{account}"
    "<p>اتصال این تلگرام به حساب کاربری بالا را تأیید کنید.</p>"
)
ACCOUNT_LINE = f"<p><b>{emoji.ACCOUNT} حساب کاربری:</b> <code>{{label}}</code></p>"
CONFIRM_BUTTON = f"{emoji.CONFIRM} تأیید اتصال"
LINKED = (
    f"<h2>{emoji.CONFIRM} تلگرام شما به حساب PremShop وصل شد</h2>"
    "<p>خبر تأیید پرداخت، تحویل و گزارش هر سفارش از این پس اینجا هم می‌رسد.</p>"
)
ALREADY = f"<h2>{emoji.CONFIRM} این تلگرام به حساب PremShop شما وصل است</h2>"
SLOW = "کمی صبر کنید و دوباره بزنید."
COMMAND = "پنل سفارش‌ها"

Buttons = list[list[dict[str, Any]]]
Screen = tuple[str, Buttons]
# The orders app draws the panel's screens, since accounts may not import orders: it
# registers `render(user, screen)` here as it starts (orders.apps).
_panel: Callable[[User, str], Screen] | None = None


def register_panel(render: Callable[[User, str], Screen]) -> None:
    global _panel
    _panel = render


def _screen(user: User, name: str) -> Screen:
    if _panel is None:
        raise RuntimeError("The bot's panel is not registered")
    return _panel(user, name)


LIMITED = (
    f"<h2>{emoji.LIMITED} محدودیت اتصال تلگرام</h2>"
    "<p>با این حساب تلگرام در هر ۲۴ ساعت بیشتر از ۵ بار نمی‌توانید اتصال را تأیید کنید. "
    "پس از پایان این بازه دوباره امتحان کنید.</p>"
)
EXPIRED = (
    f"<h2>{emoji.EXPIRED} پایان اعتبار لینک</h2>"
    "<p>برای ادامه‌ی اتصال، از صفحه‌ی حساب کاربری لینک جدیدی بگیرید.</p>"
)
# The steps are paragraphs: a rich message numbers a list in Latin digits.
POINTER = (
    f"<h2>{emoji.BOT} ربات اطلاع‌رسانی سفارش‌های PremShop</h2>"
    "<p>با اتصال حساب کاربری، خبر تأیید پرداخت، تحویل و گزارش هر سفارش اینجا هم می‌رسد.</p>"
    "<h4>اتصال در سه قدم</h4>"
    "<p>۱. در سایت وارد حساب کاربری شوید.</p>"
    "<p>۲. در صفحه‌ی حساب کاربری، «اتصال تلگرام» و بعد «باز کردن تلگرام» را بزنید.</p>"
    "<p>۳. اینجا «شروع» و بعد «تأیید اتصال» را بزنید.</p>"
    "<footer>اگر درباره‌ی خرید یا سفارشتان سؤالی دارید، از دکمه‌ی «پشتیبانی» پیام بدهید تا پیام به دست ما برسد.</footer>"
)


def _link_settings(text: str) -> dict[str, Any]:
    return {"text": text, "url": f"{_account_url()}#telegram"}


def _pointer() -> Screen:
    """The reply to anyone the bot does not know: what it is for and how to link."""
    return POINTER, [
        [{**_link_settings(f"{emoji.LINK} اتصال حساب"), "style": "primary"}],
        [
            {"text": f"{emoji.BUY} خرید از سایت", "url": settings.SITE_URL + reverse("home")},
            {"text": f"{emoji.SUPPORT} پشتیبانی", "url": support.details()["telegram_url"]},
        ],
    ]


def _expired() -> Screen:
    return EXPIRED, [[{**_link_settings(f"{emoji.LINK} دریافت پیوند جدید"), "style": "primary"}]]


def _digest(token: str) -> str:
    # Stored as an HMAC, as sign-in codes are: a cache dump must not yield a live link.
    return salted_hmac("apps.accounts.telegram.link", token).hexdigest()


def _record_key(token: str) -> str:
    return f"telegram-link:{_digest(token)}"


def _current_key(user_id: int) -> str:
    return f"telegram-link-user:{user_id}"


def _account_url() -> str:
    return settings.SITE_URL + reverse("account")


def start_link(user: User) -> str:
    """Issue the account's one live link token; issuing another replaces it."""
    token = secrets.token_urlsafe(16)
    previous = cache.get(_current_key(user.pk))
    if previous:
        cache.delete(f"telegram-link:{previous}")
    cache.set(_record_key(token), user.pk, timeout=LINK_SECONDS)
    cache.set(_current_key(user.pk), _digest(token), timeout=LINK_SECONDS)
    return token


def deep_link(token: str) -> str:
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"


def link_owner(token: str) -> User | None:
    """The account a live token would link, without spending the token."""
    user_id = cache.get(_record_key(token))
    if user_id is None or cache.get(_current_key(user_id)) != _digest(token):
        return None
    return User.objects.filter(pk=user_id, is_active=True).first()


def account_label(user: User) -> str:
    """How the bot names an account: its number half hidden, isolated left to right so
    the bullets cannot flip its halves; an account with no number, by its email; one with
    neither (its number moved to another account), by nothing."""
    if user.phone:
        return f"\u2066{mask_phone(user.phone)}\u2069"
    if not user.email:
        return ""
    local, _, domain = user.email.partition("@")
    return f"{local[:1]}•••@{domain}"


def _account_line(user: User) -> str:
    label = account_label(user)
    return ACCOUNT_LINE.format(label=escape(label)) if label else ""


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
    # The chat itself confirms the change; the mail is a second word to the address,
    # when the account has one.
    if not user.email:
        return
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
        # The bot's one green button: the customer's own yes.
        button = {"text": CONFIRM_BUTTON, "callback_data": f"link:{token}", "style": "success"}
        telegram.send_message(
            chat_id,
            CONFIRM.format(account=_account_line(user)),
            buttons=[[button]],
            rich=True,
        )
    elif command == "/start" and token:
        text, buttons = _expired()
        telegram.send_message(chat_id, text, buttons=buttons, rich=True)
    elif (linked := User.objects.filter(telegram_id=chat_id, is_active=True).first()) is not None:
        # Messages spend the presses' budget: a panel costs queries and Telegram calls, and
        # a scripted /start loop would keep the one poll loop from other customers.
        if not ratelimit.hit(ratelimit.TELEGRAM_PANEL_PRESSES, str(chat_id)):
            return
        _open_panel(chat_id, linked)
        if command.startswith("/"):
            # The customer's own command goes too: the chat keeps the panel and the notices.
            with suppress(telegram.TelegramError):
                telegram.delete_message(chat_id, int(message["message_id"]))
    else:
        text, buttons = _pointer()
        telegram.send_message(chat_id, text, buttons=buttons, rich=True)


def _show(callback: dict[str, Any], chat_id: int, message_id: int, screen: Screen) -> None:
    """Draw the pressed message's next screen, then answer the press.

    The edit replaces the pressed button, so the customer sees the result after one round
    trip to Telegram rather than two; the answer only ends Telegram's wait, and goes even
    when the edit is refused.
    """
    text, buttons = screen
    try:
        telegram.edit_message(chat_id, message_id, text, buttons=buttons, rich=True)
    finally:
        _answer(callback["id"])


def _pressed(callback: dict[str, Any]) -> None:
    message = callback.get("message") or {}
    chat_id = _private_chat(message)
    if chat_id is None:
        return
    sender = callback["from"]
    data = str(callback.get("data") or "")
    if data.startswith("p:"):
        _pressed_panel(callback, chat_id, int(message["message_id"]), data.removeprefix("p:"))
        return
    token = data.removeprefix("link:")
    user: User | None = None
    screen: Screen
    if link_owner(token) is not None and not ratelimit.hit(
        ratelimit.TELEGRAM_CONFIRMS_PER_TELEGRAM, str(sender["id"])
    ):
        screen = (LIMITED, [])
    elif (user := confirm_link(token, sender)) is not None:
        screen = (LINKED, [[_link_settings(f"{emoji.LINK} مدیریت اتصال")]])
    elif User.objects.filter(telegram_id=sender["id"]).exists():
        screen = (ALREADY, [])
    else:
        screen = _expired()
    try:
        _show(callback, chat_id, int(message["message_id"]), screen)
    finally:
        # The panel opens beneath as a message of its own, so the link's note stays above
        # it; a refused edit does not undo the link, so the panel still opens.
        if user is not None:
            _open_panel(chat_id, user)


def _answer(callback_id: str, text: str = "") -> None:
    # A press queued through an outage is answered past Telegram's window, which refuses
    # it; the press itself must still be carried out.
    with suppress(telegram.TelegramError):
        telegram.answer_callback(callback_id, text=text)


def _panel_key(chat_id: int) -> str:
    return f"telegram-panel:{chat_id}"


def _open_panel(chat_id: int, user: User) -> None:
    text, buttons = _screen(user, "home")
    sent = telegram.send_message(chat_id, text, buttons=buttons, rich=True)
    previous = cache.get(_panel_key(chat_id))
    cache.set(_panel_key(chat_id), sent["message_id"], timeout=None)
    if previous:
        _retire(chat_id, previous)


def _retire(chat_id: int, message_id: int) -> None:
    """An old panel goes; past Telegram's 48 hours for deleting, it loses its buttons."""
    try:
        telegram.delete_message(chat_id, message_id)
    except telegram.TelegramError:
        with suppress(telegram.TelegramError):  # already gone: the customer deleted it
            telegram.edit_buttons(chat_id, message_id, [])


def _pressed_panel(callback: dict[str, Any], chat_id: int, message_id: int, screen: str) -> None:
    presser = int(callback["from"]["id"])
    # Every press asks again whose Telegram this is: the button itself proves nothing.
    user = User.objects.filter(telegram_id=presser, is_active=True).first()
    if user is None:
        _show(callback, chat_id, message_id, _pointer())
        return
    if not ratelimit.hit(ratelimit.TELEGRAM_PANEL_PRESSES, str(presser)):
        _answer(callback["id"], SLOW)
        return
    _show(callback, chat_id, message_id, _screen(user, screen))


def _list_commands() -> None:
    # Once a day: the menu beside the message box lists /start.
    if not cache.add(COMMANDS_KEY, True, timeout=24 * 60 * 60):
        return
    commands = [{"command": "start", "description": COMMAND}]
    try:
        telegram.call(
            "setMyCommands", {"commands": commands, "scope": {"type": "all_private_chats"}}
        )
    except telegram.TelegramError:
        cache.delete(COMMANDS_KEY)  # the next poll window tries again


def poll(seconds: float = 55) -> int:
    """Fetch and answer the bot's updates for about `seconds`. Returns how many arrived.

    Each update's offset is kept the moment it is handled, so a crash replays at most
    the update in hand. One outage logs one ERROR, which error tracking turns into one
    event, then WARNINGs until Telegram answers again.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        return 0
    _list_commands()
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
            except telegram.TelegramError as error:
                # A refused reply, such as a 429 in a burst: worth a breadcrumb, not an event each.
                logger.warning(
                    "telegram reply for update %s failed: TelegramError %s",
                    update.get("update_id"),
                    error.status or "network",
                )
            except Exception as error:  # noqa: BLE001 - one bad update must not block the rest
                logger.error(
                    "telegram update %s failed: %s", update.get("update_id"), type(error).__name__
                )
            cache.set(OFFSET_KEY, int(update["update_id"]) + 1, timeout=None)
            received += 1
        if time.monotonic() >= deadline:
            return received

"""Authentication orchestration.

Views in this project are thin controllers: they parse a form, call exactly one
service, and render. Everything that decides *what may happen* lives here, so the
rules are in one place and testable without an HTTP request. That is the service
layer pattern, and the reason for it is that auth rules leak otherwise — one
forgotten rate-limit call in one view is the whole defence gone.

One way in per account (ADR-0031): a customer signs in with a mobile number and a
code sent by SMS and has no password, and staff sign in only with the panel's
password, because a staff session opens the panel.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from functools import partial

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html

from apps.accounts import otp, telegram_codes
from apps.accounts.backends import CODE_SIGN_IN
from apps.accounts.models import User
from apps.accounts.validators import normalize_mobile_phone
from apps.core import ratelimit, sms, support
from apps.core.email import send_templated_email
from apps.core.sessions import login_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodeRequest:
    expires_at: int
    channel: str

# Unknown and inactive accounts must cost the same hash work as a live account.
_DUMMY_HASH = make_password(secrets.token_urlsafe(16))


class RateLimited(Exception):
    """Raised when a budget in ratelimit.py is spent. Carries Persian copy: the
    message a customer sees is part of the behaviour, not a view's decoration."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_CODE_NOT_SENT = (
    "کد ورود فرستاده نشد. چند دقیقه دیگر دوباره امتحان کنید یا در تلگرام به پشتیبانی پیام بدهید: {}"
)
_CODES_PAUSED = "فرستادن کد ورود فعلاً متوقف است. در تلگرام به پشتیبانی پیام بدهید: {}"


class CodeNotSent(Exception):
    """SMS.ir refused or did not answer, or the shop's day of SMS is spent. Answered
    on the page, as a refused send is; no daily unit is spent, since only accepted
    sends count."""

    def __init__(self, sentence: str = _CODE_NOT_SENT) -> None:
        handle = support.details()["telegram"]
        # The Latin handle must be isolated left-to-right, or its @ is drawn at
        # the wrong end. A page gets <bdi>; a script sets `text` as text, so it
        # carries the two Unicode marks <bdi> stands for.
        self.message = format_html(sentence, format_html('<bdi class="latin">{}</bdi>', handle))
        self.text = sentence.format(f"\u2066{handle}\u2069")
        super().__init__(self.message)


# One message per budget, each with its wait. A budget per IP never blames the
# visitor: a mobile carrier puts many customers behind one address.
CODE_COOLDOWN = (
    "برای این شماره کمتر از دو دقیقه پیش کد خواسته شده است. دو دقیقه دیگر دوباره امتحان کنید."
)
TOO_MANY_CODES = (
    "برای این شماره چند بار پشت سر هم کد خواسته شده است. یک ساعت دیگر دوباره امتحان کنید."
)
TOO_MANY_CODES_FROM_NETWORK = "از این شبکه زیاد کد خواسته شده است. یک ساعت دیگر دوباره امتحان کنید."
TOO_MANY_CODES_DAILY = (
    "درخواست کد برای این شماره به سقف روزانه رسیده است. ۲۴ ساعت دیگر دوباره امتحان کنید."
)
TOO_MANY_CODES_FROM_NETWORK_DAILY = (
    "درخواست کد از این شبکه به سقف روزانه رسیده است. ۲۴ ساعت دیگر دوباره امتحان کنید."
)
TOO_MANY_ATTEMPTS = (
    "برای این شماره چند بار پشت سر هم تلاش شده است. ۱۵ دقیقه دیگر دوباره امتحان کنید."
)
TOO_MANY_FROM_NETWORK = "از این شبکه زیاد تلاش شده است. یک ساعت دیگر دوباره امتحان کنید."


def password_attempt_refusal(request: HttpRequest, phone: str) -> str | None:
    """Count a password attempt; the message to show when a budget is spent.

    The panel's password and the shop's code checks share these budgets under the
    same keys, so an account has one budget wherever it is guessed at."""
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_PHONE, phone):
        return TOO_MANY_ATTEMPTS
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, ratelimit.client_ip(request)):
        return TOO_MANY_FROM_NETWORK
    return None


@transaction.atomic
def authenticate_password(request: HttpRequest, phone: str, password: str) -> User | None:
    """The panel's sign-in: staff are the only accounts with a password."""
    if refusal := password_attempt_refusal(request, phone):
        raise RateLimited(refusal)
    user = User.objects.select_for_update(no_key=True).filter(phone=phone).first()
    if user is not None and user.is_active:
        authenticated = user.check_password(password)
    else:
        check_password(password, _DUMMY_HASH)
        authenticated = False
    if not authenticated:
        return None
    return user


def _code_request_budget(request: HttpRequest, phone: str) -> None:
    """Check every budget before spending any; a longer refusal is named first."""
    ip = ratelimit.client_ip(request)
    if not ratelimit.remaining(ratelimit.SMS_DAILY_BUDGET, "shop"):
        raise CodeNotSent(_CODES_PAUSED)
    short = (
        (ratelimit.OTP_REQUESTS_PER_PHONE_COOLDOWN, phone, CODE_COOLDOWN),
        (ratelimit.OTP_REQUESTS_PER_PHONE, phone, TOO_MANY_CODES),
        (ratelimit.OTP_REQUESTS_PER_IP, ip, TOO_MANY_CODES_FROM_NETWORK),
    )
    for limit, subject, message in (
        (ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, phone, TOO_MANY_CODES_DAILY),
        (ratelimit.OTP_REQUESTS_PER_IP_DAILY, ip, TOO_MANY_CODES_FROM_NETWORK_DAILY),
        *short,
    ):
        if not ratelimit.remaining(limit, subject):
            raise RateLimited(message)
    for limit, subject, message in short:
        if not ratelimit.hit(limit, subject):
            raise RateLimited(message)


def _send_code(phone: str) -> otp.Issued:
    """Text a code, or raise CodeNotSent. Logged by SMS.ir's statuses, never by the
    number or the code."""
    try:
        with transaction.atomic():
            User.objects.select_for_update(no_key=True).filter(phone=phone).first()
            issued = otp.send_login_code(phone)
            cache.delete(telegram_codes.pending_key(phone))
            return issued
    except sms.SmsNotSent as error:
        logger.error(
            "code SMS not sent: HTTP %s, SMS.ir status %s",
            error.status or "none",
            "none" if error.provider_status is None else error.provider_status,
        )
        raise CodeNotSent from None


def _code_sent(request: HttpRequest, phone: str) -> None:
    # shortcut: preflight can race; short windows bound concurrent daily overshoot.
    ratelimit.hit(ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, phone)
    ratelimit.hit(ratelimit.OTP_REQUESTS_PER_IP_DAILY, ratelimit.client_ip(request))
    # One send of each window is the 100th, so the operator hears once a window,
    # however many sends cross the mark at once.
    if ratelimit.count(ratelimit.SMS_DAILY_BUDGET, "shop") == ratelimit.SMS_ALERT_AT:
        _alert_operator(ratelimit.SMS_ALERT_AT)


def _alert_operator(sent: int) -> None:
    """The operator hears, at the shop's support address, that a day's SMS ran high."""
    try:
        send_templated_email(
            "sms_budget",
            to=[settings.SUPPORT_EMAIL],
            context={
                "sent": sent,
                "ceiling": ratelimit.SMS_DAILY_BUDGET.times,
                "at": timezone.now(),
            },
        )
    except Exception as error:  # noqa: BLE001 — an alert cannot undo a code already sent
        logger.error("SMS budget alert not sent: %s", type(error).__name__)


def request_login_code(request: HttpRequest, phone: str, *, channel: str = "sms") -> CodeRequest:
    """Text a sign-in code, or raise RateLimited or CodeNotSent.

    A code goes to any valid number, known or not. Two reasons, and they point
    the same way:

    - One flow does both jobs: an unknown number gets a code, and verifying it
      CREATES the account. Registration and sign-in are the same three steps,
      which matters for a customer base that migrated from a Telegram chat and has
      no patience for a signup form.
    - It removes the user-enumeration oracle for free. If unknown numbers were
      silently dropped, the response time alone would tell an attacker who shops
      here — and for a shop selling other people's subscriptions, the customer
      list is itself sensitive.

    A staff number gets a code too, for the same reason; the code just cannot sign
    it in. Short request windows bound bursts; daily budgets count accepted sends
    only.
    """
    phone = normalize_mobile_phone(phone)
    if channel == "telegram":
        return _request_telegram_code(request, phone)
    if channel != "sms":
        raise ValueError("Unknown code channel")
    _code_request_budget(request, phone)

    issued = _send_code(phone)
    _code_sent(request, phone)
    return CodeRequest(expires_at=issued.expires_at, channel="sms")


def _request_telegram_code(request: HttpRequest, phone: str) -> CodeRequest:
    from apps.accounts.tasks import send_telegram_code

    budgets = (
        (ratelimit.TELEGRAM_CODE_COOLDOWN, phone, CODE_COOLDOWN),
        (ratelimit.TELEGRAM_CODES_PER_PHONE, phone, TOO_MANY_CODES),
        (ratelimit.TELEGRAM_CODES_PER_IP, ratelimit.client_ip(request), TOO_MANY_CODES_FROM_NETWORK),
    )
    for limit, subject, message in budgets:
        if not ratelimit.remaining(limit, subject):
            raise RateLimited(message)
    for limit, subject, message in budgets:
        if not ratelimit.hit(limit, subject):
            raise RateLimited(message)
    request_id = secrets.token_urlsafe(16)
    expires_at = int(time.time()) + otp.CODE_TTL_SECONDS
    key = telegram_codes.pending_key(phone)
    with transaction.atomic():
        user = User.objects.select_for_update(no_key=True).filter(phone=phone).first()
        previous = cache.get(key)
        cache.set(key, request_id, timeout=otp.CODE_TTL_SECONDS)
        try:
            send_telegram_code(phone, request_id, expires_at, telegram_codes.link_generation(user))
        except Exception as error:  # noqa: BLE001 - queue details can contain credentials
            if previous:
                cache.set(key, previous, timeout=otp.CODE_TTL_SECONDS)
            else:
                cache.delete(key)
            logger.error("Telegram code not queued: %s", type(error).__name__)
            raise CodeNotSent from None
    return CodeRequest(expires_at=expires_at, channel="telegram")


def complete_code_login(request: HttpRequest, phone: str, code: str) -> User | None:
    """Verify a code and sign the customer in. Returns None on a wrong code.

    A right code for a staff account gets the same None: a staff session opens the
    operator panel, which only the panel's password may open, so a stolen or
    recycled SIM is not the panel's key.
    """
    phone = normalize_mobile_phone(phone)
    ip = ratelimit.client_ip(request)

    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_PHONE, phone):
        raise RateLimited(TOO_MANY_ATTEMPTS)
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, ip):
        raise RateLimited(TOO_MANY_FROM_NETWORK)

    with login_session(request):
        user = User.objects.select_for_update(no_key=True).filter(phone=phone).first()
        record = otp.consume(phone, code, telegram_link=telegram_codes.link_generation(user))
        if record is None:
            return None
        if user is None:
            user = User.objects.create_user(phone=phone, is_verified=True)
        elif not user.is_active or user.is_staff:
            return None
        django_login(request, user, backend=CODE_SIGN_IN)
        user.mark_verified()
        cache.delete(telegram_codes.pending_key(phone))
        if message := record.get("telegram_message"):
            transaction.on_commit(partial(_queue_code_deletion, *message))

    ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_PHONE, phone)
    return user


def _queue_code_deletion(chat_id: int, message_id: int) -> None:
    from apps.accounts.tasks import delete_login_message

    try:
        delete_login_message(chat_id, message_id)
    except Exception as error:  # noqa: BLE001 - cleanup cannot undo sign-in
        logger.error("Telegram code deletion not queued: %s", type(error).__name__)

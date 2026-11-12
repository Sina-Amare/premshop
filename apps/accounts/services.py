"""Authentication orchestration.

Views in this project are thin controllers: they parse a form, call exactly one
service, and render. Everything that decides *what may happen* lives here, so the
rules are in one place and testable without an HTTP request. That is the service
layer pattern, and the reason for it is that auth rules leak otherwise — one
forgotten rate-limit call in one view is the whole defence gone.

One way in per account: a customer signs in by code and has no password, and staff
sign in only with the panel's password, because a staff session opens the panel.
"""

from __future__ import annotations

import logging
import secrets

from django.contrib.auth import login as django_login
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import HttpRequest
from django.utils.html import format_html

from apps.accounts import otp
from apps.accounts.models import User
from apps.core import ratelimit, support
from apps.core.sessions import login_session

logger = logging.getLogger(__name__)

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


class CodeNotSent(Exception):
    """The relay failed or accepted nothing. Answered on the page, as a refused
    send is; no daily unit is spent, since only accepted sends count."""

    def __init__(self) -> None:
        handle = support.details()["telegram"]
        # The Latin handle must be isolated left-to-right, or its @ is drawn at
        # the wrong end. A page gets <bdi>; a script sets `text` as text, so it
        # carries the two Unicode marks <bdi> stands for.
        self.message = format_html(
            _CODE_NOT_SENT, format_html('<bdi class="latin">{}</bdi>', handle)
        )
        self.text = _CODE_NOT_SENT.format(f"⁦{handle}⁩")
        super().__init__(self.message)


# One message per budget, each with its wait. A budget per IP never blames the
# visitor: a mobile carrier puts many customers behind one address.
TOO_MANY_CODES = (
    "برای این ایمیل چند بار پشت سر هم کد خواسته شده است. ۱۵ دقیقه دیگر دوباره امتحان کنید."
)
TOO_MANY_CODES_FROM_NETWORK = "از این شبکه زیاد کد خواسته شده است. یک ساعت دیگر دوباره امتحان کنید."
TOO_MANY_CODES_DAILY = (
    "درخواست کد برای این ایمیل به سقف روزانه رسیده است. ۲۴ ساعت دیگر دوباره امتحان کنید."
)
TOO_MANY_CODES_FROM_NETWORK_DAILY = (
    "درخواست کد از این شبکه به سقف روزانه رسیده است. ۲۴ ساعت دیگر دوباره امتحان کنید."
)
TOO_MANY_ATTEMPTS = (
    "برای این ایمیل چند بار پشت سر هم تلاش شده است. ۱۵ دقیقه دیگر دوباره امتحان کنید."
)
TOO_MANY_FROM_NETWORK = "از این شبکه زیاد تلاش شده است. یک ساعت دیگر دوباره امتحان کنید."


def password_attempt_refusal(request: HttpRequest, email: str) -> str | None:
    """Count a password attempt; the message to show when a budget is spent.

    The panel's password and the shop's code checks share these budgets under the
    same keys, so an account has one budget wherever it is guessed at."""
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email):
        return TOO_MANY_ATTEMPTS
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, ratelimit.client_ip(request)):
        return TOO_MANY_FROM_NETWORK
    return None


@transaction.atomic
def authenticate_password(request: HttpRequest, email: str, password: str) -> User | None:
    """The panel's sign-in: staff are the only accounts with a password."""
    email = otp.normalize_email(email)
    if refusal := password_attempt_refusal(request, email):
        raise RateLimited(refusal)
    user = User.objects.select_for_update(no_key=True).filter(email__iexact=email).first()
    if user is not None and user.is_active:
        authenticated = user.check_password(password)
    else:
        check_password(password, _DUMMY_HASH)
        authenticated = False
    if not authenticated:
        return None
    return user


def _code_request_budget(request: HttpRequest, email: str) -> None:
    ip = ratelimit.client_ip(request)
    for limit, subject, message in (
        (ratelimit.OTP_REQUESTS_PER_EMAIL_DAILY, email, TOO_MANY_CODES_DAILY),
        (ratelimit.OTP_REQUESTS_PER_IP_DAILY, ip, TOO_MANY_CODES_FROM_NETWORK_DAILY),
        (ratelimit.OTP_REQUESTS_PER_EMAIL, email, TOO_MANY_CODES),
        (ratelimit.OTP_REQUESTS_PER_IP, ip, TOO_MANY_CODES_FROM_NETWORK),
    ):
        if not ratelimit.remaining(limit, subject):
            raise RateLimited(message)
    if not ratelimit.hit(ratelimit.OTP_REQUESTS_PER_EMAIL, email):
        raise RateLimited(TOO_MANY_CODES)
    if not ratelimit.hit(ratelimit.OTP_REQUESTS_PER_IP, ratelimit.client_ip(request)):
        raise RateLimited(TOO_MANY_CODES_FROM_NETWORK)


def _send_code(email: str) -> otp.Issued:
    """Mail a code, or raise CodeNotSent. Logged by the error's type and SMTP
    status, never its text, which can quote the address."""
    try:
        return otp.send_login_code(email)
    except OSError as error:  # smtplib's errors are OSError, as are socket's
        status = getattr(error, "smtp_code", None)
        logger.error(
            "code email not sent: %s%s",
            type(error).__name__,
            f" (SMTP {status})" if isinstance(status, int) else "",
        )
        raise CodeNotSent from None


def _code_sent(request: HttpRequest, email: str) -> None:
    # shortcut: preflight can race; short windows bound concurrent daily overshoot.
    ratelimit.hit(ratelimit.OTP_REQUESTS_PER_EMAIL_DAILY, email)
    ratelimit.hit(ratelimit.OTP_REQUESTS_PER_IP_DAILY, ratelimit.client_ip(request))


def request_login_code(request: HttpRequest, email: str) -> otp.Issued:
    """Mail a login code, or raise RateLimited.

    A code goes to any valid address, known or not. Two reasons, and they point
    the same way:

    - ADR-0012 makes one flow do both jobs: an unknown address gets a code, and
      verifying it CREATES the account. Registration and login are the same three
      clicks, which matters for a customer base that migrated from a Telegram chat
      and has no patience for a signup form.
    - It removes the user-enumeration oracle for free. If unknown addresses were
      silently dropped, the response time alone would tell an attacker who shops
      here — and for a shop selling other people's subscriptions, the customer
      list is itself sensitive.

    A staff address gets a code too, for the same reason; the code just cannot
    sign it in. Short request windows bound bursts; daily budgets count accepted
    sends only.
    """
    email = otp.normalize_email(email)
    _code_request_budget(request, email)

    issued = _send_code(email)
    _code_sent(request, email)
    return issued


def complete_code_login(request: HttpRequest, email: str, code: str) -> User | None:
    """Verify a code and sign the customer in. Returns None on a wrong code.

    A right code for a staff account gets the same None: a staff session opens the
    operator panel, which only the panel's password may open, so a stolen mailbox
    or SIM is not the panel's key.
    """
    email = otp.normalize_email(email)
    ip = ratelimit.client_ip(request)

    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email):
        raise RateLimited(TOO_MANY_ATTEMPTS)
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, ip):
        raise RateLimited(TOO_MANY_FROM_NETWORK)

    if not otp.verify(email, code):
        return None

    user = otp.user_for(email)
    if user is None:
        # First correct code for this address: the account is created here, and
        # is_verified is true from birth because the code proved the mailbox.
        user = User.objects.create_user(email=email, password=None, is_verified=True)
    elif not user.is_active or user.is_staff:
        return None

    # django_login cycles the session key, which is the whole defence against
    # session fixation — an attacker who planted a known session id in the
    # browser before login cannot reuse it after.
    with login_session(request):
        django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    # Receiving the code proves the address works, which is the only claim email
    # verification ever makes. Hence no separate "click to verify" mail (ADR-0012).
    user.mark_verified()

    ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email)
    return user

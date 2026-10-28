"""Authentication orchestration.

Views in this project are thin controllers: they parse a form, call exactly one
service, and render. Everything that decides *what may happen* lives here, so the
rules are in one place and testable without an HTTP request. That is the service
layer pattern, and the reason for it is that auth rules leak otherwise — one
forgotten rate-limit call in one view is the whole defence gone.
"""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY, update_session_auth_hash
from django.contrib.auth import login as django_login
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.models import User
from apps.core import ratelimit
from apps.core.email import send_templated_email
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


# One message per budget, each with its wait. A budget per IP never blames the
# visitor: a mobile carrier puts many customers behind one address.
TOO_MANY_CODES = (
    "برای این ایمیل چند بار پشت سر هم کد خواسته شده است. ۱۵ دقیقه دیگر دوباره امتحان کنید."
)
TOO_MANY_CODES_FROM_NETWORK = (
    "از این شبکه کد ورود زیادی خواسته شده است. یک ساعت دیگر دوباره امتحان کنید."
)
TOO_MANY_WRONG_CODES = (
    "برای این ایمیل چند بار پشت سر هم کد اشتباه وارد شده است. ۱۵ دقیقه دیگر دوباره امتحان کنید."
)
TOO_MANY_WRONG_PASSWORDS = (
    "برای این ایمیل چند بار پشت سر هم رمز اشتباه وارد شده است. ۱۵ دقیقه دیگر دوباره امتحان کنید."
)
TOO_MANY_FROM_NETWORK = "از این شبکه تلاش ورود زیادی شده است. یک ساعت دیگر دوباره امتحان کنید."


def password_attempt_refusal(request: HttpRequest, email: str) -> str | None:
    """Count a password attempt; the message to show when a budget is spent.

    The storefront's sign-in and the panel's share these budgets under the same
    keys, so an account has one budget wherever it is guessed at."""
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email):
        return TOO_MANY_WRONG_PASSWORDS
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, ratelimit.client_ip(request)):
        return TOO_MANY_FROM_NETWORK
    return None


def login_with_password(request: HttpRequest, email: str, password: str) -> User | None:
    email = otp.normalize_email(email)
    if refusal := password_attempt_refusal(request, email):
        raise RateLimited(refusal)
    user = otp.user_for(email)
    if user is not None and user.is_active:
        authenticated = user.check_password(password)
    else:
        check_password(password, _DUMMY_HASH)
        authenticated = False
    if not authenticated:
        return None
    with login_session(request):
        django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email)
    return user


def _code_request_budget(request: HttpRequest, email: str) -> None:
    if not ratelimit.hit(ratelimit.OTP_REQUESTS_PER_EMAIL, email):
        raise RateLimited(TOO_MANY_CODES)
    if not ratelimit.hit(ratelimit.OTP_REQUESTS_PER_IP, ratelimit.client_ip(request)):
        raise RateLimited(TOO_MANY_CODES_FROM_NETWORK)


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

    The abuse this opens — making us mail a stranger — is what the two rate limits
    are for: three per address per fifteen minutes, ten per IP per hour.
    """
    email = otp.normalize_email(email)
    _code_request_budget(request, email)

    return otp.send_login_code(email)


def _recovery_user(request: HttpRequest) -> User:
    user_id = request.user.pk
    if user_id is None:
        raise PermissionDenied
    user = User.objects.select_for_update(no_key=True).filter(pk=user_id, is_active=True).first()
    if (
        user is None
        or request.session.get(SESSION_KEY) != str(user.pk)
        or request.session.get(HASH_SESSION_KEY) != user.get_session_auth_hash()
    ):
        raise PermissionDenied
    return user


def _recovery_binding(request: HttpRequest, user: User) -> str:
    return f"{request.session.session_key}:{user.get_session_auth_hash()}"


def request_password_recovery(request: HttpRequest) -> otp.Issued:
    with login_session(request):
        user = _recovery_user(request)
        _code_request_budget(request, user.email)
        return otp.send_login_code(
            user.email, purpose="password_reset", binding=_recovery_binding(request, user)
        )


def complete_password_recovery(request: HttpRequest, *, code: str, password: str) -> User | None:
    with login_session(request):
        user = _recovery_user(request)
        validate_password(password, user)
        if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, user.email):
            raise RateLimited(TOO_MANY_WRONG_CODES)
        if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, ratelimit.client_ip(request)):
            raise RateLimited(TOO_MANY_FROM_NETWORK)
        if not otp.verify(
            user.email, code, purpose="password_reset", binding=_recovery_binding(request, user)
        ):
            return None
        _replace_password(request, user, password)
        ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, user.email)
    return user


def change_password(request: HttpRequest, *, password: str, old_password: str = "") -> User:
    with login_session(request):
        user = _recovery_user(request)
        if user.has_usable_password() and not user.check_password(old_password):
            raise ValidationError("رمز عبور فعلی اشتباه است.")
        validate_password(password, user)
        _replace_password(request, user, password)
    return user


def _replace_password(request: HttpRequest, user: User, password: str) -> None:
    user.set_password(password)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    request.session.save()
    transaction.on_commit(lambda: _send_password_notice(user))


def _send_password_notice(user: User) -> None:
    try:
        send_templated_email(
            "password_changed", to=[user.email], context={"changed_at": timezone.now()}
        )
    except Exception as error:  # noqa: BLE001 — notification cannot undo the password change
        status = getattr(error, "smtp_code", None)
        logger.error(
            "password change notice not sent for user %s: %s%s",
            user.pk,
            type(error).__name__,
            f" (SMTP {status})" if isinstance(status, int) else "",
        )


def complete_code_login(request: HttpRequest, email: str, code: str) -> User | None:
    """Verify a code and sign the user in. Returns None on a wrong code."""
    email = otp.normalize_email(email)
    ip = ratelimit.client_ip(request)

    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email):
        raise RateLimited(TOO_MANY_WRONG_CODES)
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, ip):
        raise RateLimited(TOO_MANY_FROM_NETWORK)

    if not otp.verify(email, code):
        return None

    user = otp.user_for(email)
    if user is None:
        # First correct code for this address: the account is created here, and
        # is_verified is true from birth because the code proved the mailbox.
        user = User.objects.create_user(email=email, password=None, is_verified=True)
    elif not user.is_active:
        return None

    had_password = user.has_usable_password()

    # django_login cycles the session key, which is the whole defence against
    # session fixation — an attacker who planted a known session id in the
    # browser before login cannot reuse it after.
    with login_session(request):
        django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    # Receiving the code proves the address works, which is the only claim email
    # verification ever makes. Hence no separate "click to verify" mail (ADR-0012).
    user.mark_verified()

    ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email)

    if had_password:
        _send_signin_alert(user)
    return user


def _send_signin_alert(user: User) -> None:
    """C14. Only for accounts that HAVE a password: an OTP-only account has no
    second factor for this notice to protect, so it would be noise on every login.

    A failure here must not fail the login. The customer is already authenticated,
    holding a session and a spent code; refusing that because a notification
    bounced would turn a mail problem into a lockout. So a failure is logged, never
    raised — and never swallowed silently either.

    How the log line is shaped is the whole point, and a test holds it there:
    - ERROR, not WARNING. Error tracking turns ERROR records into issues and
      keeps anything lower only as a breadcrumb, so a warning would be silent.
    - No traceback and no exception text. SMTP errors quote the recipient — a
      "recipients refused" error is a dict keyed by the address, and relays echo
      it in their replies — and a traceback's local variables hold `to=[email]`.
      The account id, the error's type and the SMTP status number are enough to
      act on; the relay's own log has the rest (ADR-0008).
    """
    try:
        send_templated_email(
            "signin_alert",
            to=[user.email],
            context={
                "signed_in_at": timezone.now(),
                # SITE_URL and not the Host header, which the client chooses.
                "password_url": settings.SITE_URL + reverse("account-password"),
            },
        )
    except Exception as error:  # noqa: BLE001 — any mail failure; the login already happened
        status = getattr(error, "smtp_code", None)
        logger.error(
            "sign-in alert not sent for user %s: %s%s",
            user.pk,
            type(error).__name__,
            f" (SMTP {status})" if isinstance(status, int) else "",
        )

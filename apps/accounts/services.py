"""Authentication orchestration.

Views in this project are thin controllers: they parse a form, call exactly one
service, and render. Everything that decides *what may happen* lives here, so the
rules are in one place and testable without an HTTP request. That is the service
layer pattern, and the reason for it is that auth rules leak otherwise — one
forgotten rate-limit call in one view is the whole defence gone.
"""

from __future__ import annotations

from django.contrib.auth import login as django_login
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.models import User
from apps.core import ratelimit
from apps.core.email import send_templated_email


class RateLimited(Exception):
    """Raised when a budget in ratelimit.py is spent. Carries Persian copy: the
    message a customer sees is part of the behaviour, not a view's decoration."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


TOO_MANY_CODES = "چند بار پشت سر هم کد خواسته‌اید. چند دقیقه بعد دوباره تلاش کنید."
TOO_MANY_ATTEMPTS = "چند بار پشت سر هم اشتباه وارد کرده‌اید. چند دقیقه بعد دوباره تلاش کنید."


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
    ip = ratelimit.client_ip(request)

    if not ratelimit.hit(ratelimit.OTP_REQUESTS_PER_EMAIL, email):
        raise RateLimited(TOO_MANY_CODES)
    if not ratelimit.hit(ratelimit.OTP_REQUESTS_PER_IP, ip):
        raise RateLimited(TOO_MANY_CODES)

    return otp.send_login_code(email)


def complete_code_login(request: HttpRequest, email: str, code: str) -> User | None:
    """Verify a code and sign the user in. Returns None on a wrong code."""
    email = otp.normalize_email(email)
    ip = ratelimit.client_ip(request)

    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email):
        raise RateLimited(TOO_MANY_ATTEMPTS)
    if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_IP, ip):
        raise RateLimited(TOO_MANY_ATTEMPTS)

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
    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    # Receiving the code proves the address works, which is the only claim email
    # verification ever makes. Hence no separate "click to verify" mail (ADR-0012).
    user.mark_verified()

    ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email)

    if had_password:
        _send_signin_alert(request, user)
    return user


def _send_signin_alert(request: HttpRequest, user: User) -> None:
    """C14. Only for accounts that HAVE a password: an OTP-only account has no
    second factor for this notice to protect, so it would be noise on every login.

    A failure here must not fail the login. The customer is already authenticated
    and holding a session; refusing that because a notification bounced would turn
    a mail problem into a lockout.
    """
    send_templated_email(
        "signin_alert",
        to=[user.email],
        context={
            "signed_in_at": timezone.now(),
            "account_url": request.build_absolute_uri(reverse("account")),
        },
    )

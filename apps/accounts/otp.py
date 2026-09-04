"""One-time login codes.

Codes live in the cache with a TTL, not in a table: an expired code should
evaporate rather than accumulate as dead rows for someone to sweep, and there is
no state here worth keeping after the fact — the audit trail that matters is the
session it creates, not the code that created it.

The code is stored HASHED. A cache dump is a bad day either way, but a plaintext
six-digit code next to an email address in a monitoring tool or a log line is a
live credential lying in the open, and hashing costs nothing to avoid it.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.accounts.models import User

logger = logging.getLogger(__name__)

PERSIAN_TO_ASCII_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

CODE_LENGTH = 6
CODE_TTL_SECONDS = 10 * 60  # Owner ruling 2026-09-04; contract C12 updated to match.
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Issued:
    code: str
    ttl_minutes: int


def _key(email: str) -> str:
    return f"otp:{email.lower().strip()}"


def _digest(code: str) -> str:
    """Keyed hash, so a cache dump alone does not read as a live code."""
    return salted_hmac("premshop.accounts.otp", code, secret=settings.SECRET_KEY).hexdigest()


def issue(email: str) -> Issued:
    """Generate a code, replacing any outstanding one for this address.

    Replacing rather than reusing matters: a customer who clicks "send again"
    expects the newest mail to be the one that works. Two live codes would mean
    the older mail silently fails, which reads as a broken shop.

    secrets, not random: random is seeded predictably and is not for anything
    that guards an account.
    """
    code = f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"
    cache.set(_key(email), {"digest": _digest(code), "attempts": 0}, timeout=CODE_TTL_SECONDS)
    return Issued(code=code, ttl_minutes=CODE_TTL_SECONDS // 60)


def verify(email: str, code: str) -> bool:
    """Check a code and consume it. A correct code is single-use.

    Wrong guesses are counted, and the code is BURNED at MAX_ATTEMPTS rather than
    merely refused — otherwise the per-request rate limit is the only thing
    between an attacker and a million tries at a six-digit number.
    """
    key = _key(email)
    record = cache.get(key)
    if not record:
        return False

    submitted = (code or "").strip().translate(PERSIAN_TO_ASCII_DIGITS)
    if constant_time_compare(record["digest"], _digest(submitted)):
        cache.delete(key)
        return True

    record["attempts"] += 1
    if record["attempts"] >= MAX_ATTEMPTS:
        cache.delete(key)
    else:
        # Re-set without extending the deadline: a wrong guess must not buy time.
        cache.set(key, record, timeout=_remaining_ttl(key))
    return False


def _remaining_ttl(key: str) -> int:
    """How long the current code has left, floored at one second.

    Django's cache API has no portable TTL read, so this is deliberately
    conservative: on the locmem backend used in tests there is no way to ask, and
    re-setting the full TTL would let an attacker extend a code's life by guessing
    at it. One second short of correct is safe; longer than correct is not.
    """
    from django.core.cache import caches

    backend = caches["default"]
    ttl = getattr(backend, "ttl", None)
    if callable(ttl):
        try:
            return max(1, int(ttl(key) or 1))
        except Exception:  # noqa: BLE001 — a cache that cannot answer is not fatal
            logger.debug("cache backend gave no TTL for %s; using the safe floor", key)
    return max(1, CODE_TTL_SECONDS // 2)


def normalize_digits(value: str) -> str:
    """Persian ۰-۹ (and Arabic-Indic ٠-٩) to ASCII.

    The OTP email renders the code in Persian digits because that is what an
    Iranian reader expects to see. A customer who copies what they were shown
    would otherwise submit ۴۱۸۳۰۵ against a stored 418305 and be told, correctly
    and uselessly, that the code is wrong.
    """
    return (value or "").strip().translate(PERSIAN_TO_ASCII_DIGITS)


def send_login_code(email: str) -> None:
    """Issue a code and mail it. Raises if the relay refuses — never silently."""
    from apps.core.email import send_templated_email

    issued = issue(email)
    send_templated_email(
        "otp_code",
        to=[email],
        context={"code": issued.code, "ttl_minutes": issued.ttl_minutes, "login_url": ""},
    )


def normalize_email(email: str) -> str:
    """One spelling of an address everywhere: lowercase, trimmed.

    The rate limiter keys on this. Without it "Ali@" and "ali@" get separate
    budgets, and the per-email limit is bypassed by holding down shift.
    """
    return (email or "").strip().lower()


def user_for(email: str) -> User | None:
    return User.objects.filter(email__iexact=email.strip()).first()

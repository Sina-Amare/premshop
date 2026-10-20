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

import secrets
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.accounts.models import User

PERSIAN_TO_ASCII_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

CODE_LENGTH = 6
CODE_TTL_SECONDS = 10 * 60  # Owner ruling 2026-09-04; contract C12 updated to match.
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Issued:
    code: str
    ttl_minutes: int
    expires_at: int  # unix seconds; the page counts down to this


def _key(email: str) -> str:
    return f"otp:{email.lower().strip()}"


def _attempts_key(email: str) -> str:
    # Its own prefix, so no address can spell its way into the other key.
    return f"otp-attempts:{email.lower().strip()}"


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
    expires_at = int(time.time()) + CODE_TTL_SECONDS
    # The deadline travels WITH the code. Django's cache API cannot read an entry's
    # remaining lifetime back, on Redis included, so the attempt counter takes its
    # lifetime from here rather than guessing it.
    record = {"digest": _digest(code), "expires_at": expires_at}
    cache.set(_key(email), record, timeout=CODE_TTL_SECONDS)
    cache.delete(_attempts_key(email))  # a new code starts with a full budget
    return Issued(code=code, ttl_minutes=CODE_TTL_SECONDS // 60, expires_at=expires_at)


def verify(email: str, code: str) -> bool:
    """Check a code and consume it. A correct code is single-use.

    Every attempt is counted before it is compared, and the wrong guess that
    spends MAX_ATTEMPTS deletes the code: otherwise the per-request rate limit
    is the only thing between an attacker and a million tries at a six-digit
    number, and a counter lost on its own would revive a guessed-out code.
    Requests arriving together read the same record, so nothing here is decided
    by what they read: the count is an atomic increment in its own key, and only
    the request whose delete removed the record has spent the code. The record
    is written by issue() alone, so a guess can never save a stale copy over a
    newer code.
    """
    key = _key(email)
    record = cache.get(key)
    if not record:
        return False

    # A record without a deadline predates it being stored; treat it as expired
    # (fail closed) rather than guess — the customer simply asks for a new code.
    remaining = record.get("expires_at", 0) - int(time.time())
    if remaining <= 0:
        return False

    attempt = _count_attempt(email, remaining)
    if attempt > MAX_ATTEMPTS:
        return False

    matches = constant_time_compare(record["digest"], _digest(normalize_digits(code)))
    if not matches and attempt == MAX_ATTEMPTS:
        cache.delete(key)
    return matches and cache.delete(key)


def _count_attempt(email: str, ttl: int) -> int:
    """This attempt's number. Two requests at the same instant get two numbers."""
    key = _attempts_key(email)
    if cache.add(key, 1, timeout=ttl):
        return 1
    try:
        count = cache.incr(key)
    except ValueError:
        # The counter expires with the code it counts, so one that vanished
        # mid-check means the code's life is over: fail closed.
        return MAX_ATTEMPTS + 1
    if count == 1:
        # It expired inside incr() instead, between Django's EXISTS and INCR
        # (ratelimit.hit), and Redis made it again with no expiry. The same end
        # of life, so the same answer.
        cache.touch(key, ttl)
        return MAX_ATTEMPTS + 1
    return count


def normalize_digits(value: str) -> str:
    """Persian ۰-۹ (and Arabic-Indic ٠-٩) to ASCII.

    The OTP email renders the code in Persian digits because that is what an
    Iranian reader expects to see. A customer who copies what they were shown
    would otherwise submit ۴۱۸۳۰۵ against a stored 418305 and be told, correctly
    and uselessly, that the code is wrong.
    """
    return (value or "").strip().translate(PERSIAN_TO_ASCII_DIGITS)


def send_login_code(email: str) -> Issued:
    """Issue a code and mail it. Raises if the relay refuses — never silently."""
    from apps.core.email import send_templated_email

    issued = issue(email)
    send_templated_email(
        "otp_code",
        to=[email],
        context={"code": issued.code, "ttl_minutes": issued.ttl_minutes, "login_url": ""},
    )
    return issued


def normalize_email(email: str) -> str:
    """One spelling of an address everywhere: lowercase, trimmed.

    The rate limiter keys on this. Without it "Ali@" and "ali@" get separate
    budgets, and the per-email limit is bypassed by holding down shift.
    """
    return (email or "").strip().lower()


def user_for(email: str) -> User | None:
    return User.objects.filter(email__iexact=email.strip()).first()

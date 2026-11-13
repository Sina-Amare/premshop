"""One-time sign-in codes, sent by SMS to a mobile number.

Codes live in the cache with a TTL, not in a table: an expired code should
evaporate rather than accumulate as dead rows for someone to sweep, and there is
no state here worth keeping after the fact — the audit trail that matters is the
session it creates, not the code that created it.

The code is stored HASHED. A cache dump is a bad day either way, but a plaintext
six-digit code next to a phone number in a monitoring tool or a log line is a
live credential lying in the open, and hashing costs nothing to avoid it.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.accounts.models import User
from apps.accounts.validators import PERSIAN_TO_ASCII_DIGITS, normalize_digits

__all__ = ["PERSIAN_TO_ASCII_DIGITS", "normalize_digits"]

CODE_LENGTH = 6
CODE_TTL_SECONDS = 10 * 60  # Owner ruling 2026-09-04; contract C12 updated to match.
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Issued:
    code: str = field(repr=False)
    ttl_minutes: int
    expires_at: int  # unix seconds; the page counts down to this


def _key(phone: str) -> str:
    return f"otp:{phone.lower().strip()}"


def _record_key(phone: str, issuance: str) -> str:
    return f"otp-code:{issuance}:{phone.lower().strip()}"


def _attempts_key(phone: str, issuance: str) -> str:
    return f"otp-attempts:{issuance}:{phone.lower().strip()}"


def _digest(code: str) -> str:
    """Keyed hash, so a cache dump alone does not read as a live code."""
    return salted_hmac("premshop.accounts.otp", code, secret=settings.SECRET_KEY).hexdigest()


def issue(phone: str) -> Issued:
    """Generate a code, replacing any outstanding one for this number.

    Replacing rather than reusing matters: a customer who presses "send again"
    expects the newest message to be the one that works. Two live codes would mean
    the older message silently fails, which reads as a broken shop.
    """
    return _store(phone, _new_code())


def _new_code() -> str:
    # secrets, not random: random is seeded predictably and is not for anything
    # that guards an account.
    return f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"


def _store(subject: str, code: str) -> Issued:
    """Make `code` the one live code for this subject, from now."""
    expires_at = int(time.time()) + CODE_TTL_SECONDS
    # The deadline travels WITH the code. Django's cache API cannot read an entry's
    # remaining lifetime back, on Redis included, so the attempt counter takes its
    # lifetime from here rather than guessing it.
    issuance = secrets.token_urlsafe(16)
    record = {"digest": _digest(code), "expires_at": expires_at}
    cache.set(_record_key(subject, issuance), record, timeout=CODE_TTL_SECONDS)
    # Written last, and the only key verify starts from: two stores at once leave
    # one live code, and the other record expires unread.
    cache.set(_key(subject), issuance, timeout=CODE_TTL_SECONDS)
    return Issued(code=code, ttl_minutes=CODE_TTL_SECONDS // 60, expires_at=expires_at)


def verify(phone: str, code: str) -> bool:
    """Check a code and consume it. A correct code is single-use.

    Every attempt is counted before it is compared, and the wrong guess that
    spends MAX_ATTEMPTS deletes the code: otherwise the per-request rate limit
    is the only thing between an attacker and a million tries at a six-digit
    number, and a counter lost on its own would revive a guessed-out code.
    Each issuance owns its record and attempt budget. A stale request can never
    delete a replacement; only the request deleting its record spends a code.
    """
    key = _key(phone)
    issuance = cache.get(key)
    if not isinstance(issuance, str):
        return False
    record_key = _record_key(phone, issuance)
    record = cache.get(record_key)
    if not record:
        return False

    # A record without a deadline predates it being stored; treat it as expired
    # (fail closed) rather than guess — the customer simply asks for a new code.
    remaining = record.get("expires_at", 0) - int(time.time())
    if remaining <= 0:
        return False

    attempt = _count_attempt(phone, issuance, remaining)
    if attempt > MAX_ATTEMPTS or cache.get(key) != issuance:
        return False

    matches = constant_time_compare(record["digest"], _digest(normalize_digits(code)))
    if not matches and attempt == MAX_ATTEMPTS:
        cache.delete(record_key)
    return bool(matches and cache.delete(record_key))


def _count_attempt(phone: str, issuance: str, ttl: int) -> int:
    """This attempt's number. Two requests at the same instant get two numbers."""
    key = _attempts_key(phone, issuance)
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


def send_login_code(phone: str) -> Issued:
    """Text a new code, then make it the live one. Raises SmsNotSent if SMS.ir
    refuses — never silently.

    Stored only once the send is accepted. Stored before, a send that then failed
    left the customer a countdown running for a code that never arrived, and
    replaced the one already on the phone. A failed send changes nothing, so that
    code keeps its own deadline and its own count of wrong guesses.
    """
    from apps.core import sms

    code = _new_code()
    sms.send_code(phone, code)
    # Two accepted at once: the later store wins, not always the newer message. One code stays live.
    return _store(phone, code)


def normalize_email(email: str) -> str:
    """One spelling of an address everywhere: lowercase, trimmed."""
    return (email or "").strip().lower()


def user_for(phone: str) -> User | None:
    return User.objects.filter(phone=phone).first()

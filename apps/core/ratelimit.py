"""Fixed-window rate limiting over the cache.

Every auth endpoint here is limited twice — once per identity and once per IP —
because either alone has an obvious hole. A per-number limit lets an attacker
spray a thousand different numbers from one machine; a per-IP limit lets a
botnet hammer one account. The pair is the point.

Fixed window rather than a sliding log: a sliding window needs a stored list of
timestamps per key, and at this shop's volume the extra precision buys nothing.
The known cost is a burst at a window boundary — up to 2x the limit across two
adjacent windows — which for "5 codes an hour" is 10 codes in a worst-case
minute. Acceptable; a sliding window is the upgrade if it ever isn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address

from django.core.cache import cache
from django.http import HttpRequest


@dataclass(frozen=True)
class Limit:
    """A budget of `times` events per `seconds`, named for its cache key prefix."""

    name: str
    times: int
    seconds: int


# Owner-approved 2026-11-12 for codes by SMS, each of which is paid for: per
# number one code each two minutes, five an hour and ten accepted a day; per
# network ten an hour and twenty accepted a day.
OTP_REQUESTS_PER_PHONE_COOLDOWN = Limit("otpreq:phone:cooldown", times=1, seconds=2 * 60)
OTP_REQUESTS_PER_PHONE = Limit("otpreq:phone", times=5, seconds=60 * 60)
OTP_REQUESTS_PER_PHONE_DAILY = Limit("otpreq:phone:daily", times=10, seconds=24 * 60 * 60)
OTP_REQUESTS_PER_IP = Limit("otpreq:ip", times=10, seconds=60 * 60)
OTP_REQUESTS_PER_IP_DAILY = Limit("otpreq:ip:daily", times=20, seconds=24 * 60 * 60)
# The shop's own day of SMS: the operator hears once at SMS_ALERT_AT, and past
# the ceiling no code goes out, so a spray of numbers cannot drain the balance.
SMS_DAILY_BUDGET = Limit("sms:daily", times=300, seconds=24 * 60 * 60)
SMS_ALERT_AT = 100
LOGIN_ATTEMPTS_PER_PHONE = Limit("login:phone", times=10, seconds=15 * 60)
LOGIN_ATTEMPTS_PER_IP = Limit("login:ip", times=30, seconds=60 * 60)
# Catalog search: a person searching does not exceed this; a scraper does.
SEARCH_PER_IP = Limit("search:ip", times=30, seconds=60)
# The header's list asks at every pause in typing, a request a letter at a
# phone's pace, so it spends a budget of its own and leaves the one above alone.
SEARCH_SUGGEST_PER_IP = Limit("search-suggest:ip", times=120, seconds=60)
# Every Telegram link change emails the account, and that mail draws on the reserve kept
# for security mail: without a bound, one scripted account could spend it for everyone.
TELEGRAM_CHANGES_PER_ACCOUNT = Limit("telegram:account", times=5, seconds=24 * 60 * 60)
TELEGRAM_CONFIRMS_PER_TELEGRAM = Limit("telegram:confirm", times=5, seconds=24 * 60 * 60)
# Each press of the bot's panel reads the database and makes two Telegram calls.
TELEGRAM_PANEL_PRESSES = Limit("telegram:panel", times=60, seconds=60)


def client_ip(request: HttpRequest) -> str:
    """The caller's address: X-Real-IP behind our own nginx, REMOTE_ADDR otherwise.

    nginx overwrites X-Real-IP with the address it saw, so behind it that header
    holds nothing the visitor wrote. X-Forwarded-For is never read: its left-most
    entry is whatever the visitor sent, and trusting it let anyone reset every
    per-IP limit in this file with each request. A value that is not one address
    did not come from nginx, so the connection's own address is used instead.
    """
    from django.conf import settings

    if settings.TRUST_PROXY_HEADERS:
        try:
            return str(ip_address(request.META.get("HTTP_X_REAL_IP", "")))
        except ValueError:
            pass
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _key(limit: Limit, subject: str) -> str:
    return f"rl:{limit.name}:{subject.lower()}"


def hit(limit: Limit, subject: str) -> bool:
    """Count one event. Returns False when the budget is already spent."""
    return count(limit, subject) <= limit.times


def count(limit: Limit, subject: str) -> int:
    """Count one event and return its number in the window. INCR hands out each
    number once, so exactly one event of a window is, say, the 100th.

    cache.add() sets the key only if absent, which is what creates the window and
    its expiry atomically. incr() then raises ValueError if the key expired
    between the two calls — a real race under load, not a theoretical one — and
    the recovery is to start a fresh window rather than to crash a login.

    Django's Redis incr() is EXISTS then INCR, and a key that expires between
    those two comes back from INCR at 1 with no expiry. A count of 1 can only
    mean that, so the window is given its expiry again.
    """
    key = _key(limit, subject)
    if cache.add(key, 1, timeout=limit.seconds):
        return 1
    try:
        number = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=limit.seconds)
        return 1
    if number == 1:
        cache.touch(key, limit.seconds)
    return int(number)


def remaining(limit: Limit, subject: str) -> int:
    return max(0, limit.times - (cache.get(_key(limit, subject)) or 0))


def reset(limit: Limit, subject: str) -> None:
    """Clear a budget. Called on a SUCCESSFUL login so a customer who fumbled
    their password twice is not still half-locked-out on the next visit."""
    cache.delete(_key(limit, subject))

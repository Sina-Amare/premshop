"""Fixed-window rate limiting over the cache.

Every auth endpoint here is limited twice — once per identity and once per IP —
because either alone has an obvious hole. A per-email limit lets an attacker
spray a thousand different addresses from one machine; a per-IP limit lets a
botnet hammer one account. The pair is the point.

Fixed window rather than a sliding log: a sliding window needs a stored list of
timestamps per key, and at this shop's volume the extra precision buys nothing.
The known cost is a burst at a window boundary — up to 2x the limit across two
adjacent windows — which for "3 login codes per 15 minutes" is 6 codes in a
worst-case minute. Acceptable; a sliding window is the upgrade if it ever isn't.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache
from django.http import HttpRequest


@dataclass(frozen=True)
class Limit:
    """A budget of `times` events per `seconds`, named for its cache key prefix."""

    name: str
    times: int
    seconds: int


# Owner-approved 2026-09-04. Tight enough to protect the relay quota (SMTP2GO
# free tier hard-rejects past 200/day), loose enough that a customer who mistypes
# twice is not locked out of the shop they just paid at.
OTP_REQUESTS_PER_EMAIL = Limit("otpreq:email", times=3, seconds=15 * 60)
OTP_REQUESTS_PER_IP = Limit("otpreq:ip", times=10, seconds=60 * 60)
LOGIN_ATTEMPTS_PER_EMAIL = Limit("login:email", times=10, seconds=15 * 60)
LOGIN_ATTEMPTS_PER_IP = Limit("login:ip", times=30, seconds=60 * 60)


def client_ip(request: HttpRequest) -> str:
    """The caller's address, trusting X-Forwarded-For only behind our own proxy.

    Taking the header unconditionally would let anyone set it and defeat every
    per-IP limit in this file, so it is read only when the deployment says a
    proxy is in front. REMOTE_ADDR is the truth otherwise.
    """
    from django.conf import settings

    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            # Left-most is the original client; the rest were added by hops.
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _key(limit: Limit, subject: str) -> str:
    return f"rl:{limit.name}:{subject.lower()}"


def hit(limit: Limit, subject: str) -> bool:
    """Count one event. Returns False when the budget is already spent.

    cache.add() sets the key only if absent, which is what creates the window and
    its expiry atomically. incr() then raises ValueError if the key expired
    between the two calls — a real race under load, not a theoretical one — and
    the recovery is to start a fresh window rather than to crash a login.
    """
    key = _key(limit, subject)
    if cache.add(key, 1, timeout=limit.seconds):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=limit.seconds)
        return True
    return count <= limit.times


def remaining(limit: Limit, subject: str) -> int:
    return max(0, limit.times - (cache.get(_key(limit, subject)) or 0))


def reset(limit: Limit, subject: str) -> None:
    """Clear a budget. Called on a SUCCESSFUL login so a customer who fumbled
    their password twice is not still half-locked-out on the next visit."""
    cache.delete(_key(limit, subject))

"""Error-report scrubbing.

This module holds the last line of defence before an error report leaves the
process (ADR-0007, ADR-0015): no credential, token, or customer input may
appear in it. Pure Python with no Django imports, so settings can use it
before the app registry is ready.

Data minimisation is the principle: never send a third party more than it
needs. The scrubber walks the whole event, so a secret nested three levels
deep in a local variable is redacted the same as one in a form field.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Type-checking only, so this module stays importable from settings before
    # anything else is loaded — and remains a plain, independently testable
    # function at runtime.
    from sentry_sdk.types import Event, Hint

REDACTED = "[redacted]"

#: Any key whose name *contains* one of these (case-insensitive) is redacted.
#: Only unambiguous words belong here — a substring match is greedy.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "otp",
    "authorization",
    "cookie",
    "csrf",
    "api_key",
    "apikey",
    "credential",
    "customer_input",
    "delivery_field",
    "card_number",
    "cvv",
    "sheba",
    "iban",
)

#: Words matched at key *boundaries* (`decrypted_value` hits, `values` does not).
#: Sentry nests every exception under `exception.values`, so a plain substring
#: rule on "value" would shred the report and leave us blind to the error —
#: while a credential really does arrive in keys like `decrypted_value`.
SENSITIVE_KEY_WORDS: frozenset[str] = frozenset(
    {
        "value",  # DeliveryField.value — the credential itself
        "auth",
        "session",
        "sessionid",
        "pin",
    }
)

#: Matched only as the entire key. "code" alone is an OTP; `status_code` and
#: `country_code` are diagnostics worth keeping.
SENSITIVE_KEY_EXACT: frozenset[str] = frozenset({"code"})

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Single-use delivery links are bearer capabilities (ADR-0008): a full URL in
#: an error report would let anyone holding the report open the credential view.
_DELIVERY_LINK_RE = re.compile(r"(/d/)[^/\s?\"']+")

_MAX_DEPTH = 12


def _key_words(key: str) -> set[str]:
    """Split a key into its words: `decryptedValue` and `decrypted_value` alike."""
    spaced = _CAMEL_BOUNDARY_RE.sub("_", key)
    return {word for word in _WORD_SPLIT_RE.split(spaced.lower()) if word}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SENSITIVE_KEY_EXACT:
        return True
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return True
    return bool(_key_words(key) & SENSITIVE_KEY_WORDS)


def redact_url(text: str) -> str:
    """Replace the token in any /d/<token>/ delivery link with a placeholder."""
    return _DELIVERY_LINK_RE.sub(r"\1" + REDACTED, text)


def scrub(data: Any, *, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys and delivery-link tokens."""
    if _depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(data, dict):
        return {
            key: REDACTED if _is_sensitive_key(str(key)) else scrub(value, _depth=_depth + 1)
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        scrubbed = [scrub(item, _depth=_depth + 1) for item in data]
        return tuple(scrubbed) if isinstance(data, tuple) else scrubbed
    if isinstance(data, str):
        return redact_url(data)
    return data


def scrub_event(event: Event, hint: Hint | None = None) -> Event:
    """`before_send` hook: scrub an error event and drop request bodies entirely.

    Returning the event (rather than None) keeps the report — we want to know
    the error happened, just never what secret was in flight when it did.
    """
    scrubbed: Any = scrub(event)
    request = scrubbed.get("request")
    if isinstance(request, dict):
        # Belt and braces: max_request_body_size="never" should already prevent
        # this, but a body here would be the single worst leak in the system.
        request.pop("data", None)
        request.pop("cookies", None)
        if isinstance(request.get("url"), str):
            request["url"] = redact_url(request["url"])
    return scrubbed

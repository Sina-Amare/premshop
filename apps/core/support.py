"""Where a customer reaches a person: one definition for every page and email.

The header, the footer, the FAQ, the account page, the error pages and every
email print these. Stated from separate literals they drift apart, and a
customer sent to a stale handle is sent to a stranger.
"""

from __future__ import annotations

from django.conf import settings

from apps.core.formatting import to_persian_digits

TELEGRAM_HANDLE = "premshop_support"
# The operator answers between these hours, every day, Tehran time.
OPENS, CLOSES = 10, 22


def details() -> dict[str, str]:
    """`support` in every template: the request context and every email."""
    opens = to_persian_digits(OPENS)
    hours_range = f"{opens} تا {to_persian_digits(CLOSES)}"
    return {
        "telegram": f"@{TELEGRAM_HANDLE}",
        "telegram_url": f"https://t.me/{TELEGRAM_HANDLE}",
        "email": settings.SUPPORT_EMAIL,
        # «هر روز، ساعت ۱۰ تا ۲۲», the glossary's form for a page to print whole.
        "hours": f"هر روز، ساعت {hours_range}",
        # For a sentence that already names the hours: «بیرون از ساعت ۱۰ تا ۲۲».
        "hours_range": hours_range,
        "opens": opens,
    }

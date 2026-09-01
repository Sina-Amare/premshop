"""Persian display helpers.

Convention (working agreement §9): Persian digits in prices and dates,
Latin digits in inputs and technical identifiers. Money is stored in toman
as an integer-valued Decimal (ADR-0005) and rendered with thousands
separators. Datetimes are stored in UTC and displayed in Tehran time as a
Jalali date — Jalali never enters the database.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import jdatetime
from django.utils import timezone

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_LATIN_TO_PERSIAN = str.maketrans("0123456789", PERSIAN_DIGITS)
_PERSIAN_TO_LATIN = str.maketrans(PERSIAN_DIGITS, "0123456789")

JALALI_MONTHS = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)


def to_persian_digits(value: object) -> str:
    """Render any value with Persian digits — display only, never for storage."""
    return str(value).translate(_LATIN_TO_PERSIAN)


def to_latin_digits(value: str) -> str:
    """Normalise user input: Persian digits typed into a form become Latin."""
    return value.translate(_PERSIAN_TO_LATIN)


def format_toman(amount: Decimal | int, *, with_unit: bool = True) -> str:
    """Format a toman amount: thousands separators, Persian digits, «تومان»."""
    grouped = f"{int(amount):,}".replace(",", "٬")  # U+066C, the Persian thousands separator
    rendered = to_persian_digits(grouped)
    return f"{rendered} تومان" if with_unit else rendered


def to_jalali(value: datetime | date) -> jdatetime.date:
    """Convert a stored datetime/date to its Jalali calendar date in Tehran time."""
    if isinstance(value, datetime):
        local = timezone.localtime(value) if timezone.is_aware(value) else value
        return jdatetime.date.fromgregorian(date=local.date())
    return jdatetime.date.fromgregorian(date=value)


def format_jalali(value: datetime | date | None, *, with_month_name: bool = True) -> str:
    """Render a Jalali date for display, e.g. «۱۰ شهریور ۱۴۰۵» or «۱۴۰۵/۰۶/۱۰»."""
    if value is None:
        return ""
    jalali = to_jalali(value)
    if with_month_name:
        return to_persian_digits(f"{jalali.day} {JALALI_MONTHS[jalali.month - 1]} {jalali.year}")
    return to_persian_digits(f"{jalali.year}/{jalali.month:02d}/{jalali.day:02d}")

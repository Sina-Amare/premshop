"""Persian display helpers.

Convention: Persian digits in prices and dates,
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


def mask_phone(phone: str) -> str:
    """A mobile number half hidden, «۰۹۱۲•••۶۷۸۹»: enough for its owner to know it,
    too little for anyone reading over a shoulder. Callers isolate it left to right,
    or the bullets flip its two halves in right-to-left text."""
    return to_persian_digits(f"{phone[:4]}•••{phone[-4:]}") if phone else ""


def format_toman(amount: Decimal | int, *, with_unit: bool = True) -> str:
    """Format a toman amount: Persian digits, thousands separators, «تومان».

    The separator is an ASCII comma, not U+066C. Iranian shops use the comma,
    it is what readers expect, and it is bidi class CS so it is absorbed safely
    in mixed text — whereas U+066C renders as a faint high comma in both of our
    faces and reads as a rendering fault (ADR-0016).
    """
    if with_unit and int(amount) == 0:
        return "رایگان"
    rendered = to_persian_digits(f"{int(amount):,}")
    return f"{rendered} تومان" if with_unit else rendered


def format_clock(seconds: int) -> str:
    """Hours, minutes and seconds left, the hours uncapped: «۱۴۸:۴۴:۰۶».

    A storefront discount counts down this way even with days to go, by the
    owner's choice (docs/design-language.md). The hour field is padded even
    at zero. «۰۸:۴۰» alone could be read as eight hours and forty minutes,
    and «۰:۰۸:۴۰» reads as a stray digit; «۰۰:۰۸:۴۰» is unambiguous and is
    the shape Iranian shops use.
    """
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return to_persian_digits(f"{hours:02d}:{minutes:02d}:{secs:02d}")


def format_countdown(seconds: int) -> str:
    """How much time is left, at the granularity the reader can act on.

    Under a day: a clock, down to the second. Above it: days and hours — in
    the operator panel a clock reading ۴۱۲:۰۶:۵۵ is a number, not a deadline.
    The hour is dropped from «۳ روز» when it is zero, because «۳ روز و ۰ ساعت»
    reads as a bug.
    """
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        days, hours = divmod(seconds // 3600, 24)
        if not hours:
            return f"{to_persian_digits(days)} روز"
        return f"{to_persian_digits(days)} روز و {to_persian_digits(hours)} ساعت"
    return format_clock(seconds)


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

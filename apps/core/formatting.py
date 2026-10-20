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


NBSP = "\u00a0"


def _count(number: int, unit: str) -> str:
    return f"{to_persian_digits(number)}{NBSP}{unit}"


def format_countdown(seconds: int) -> str:
    """Time left on a discount: «۱۳ روز و ۲۲ ساعت», under a day «۳ ساعت و ۳۹ دقیقه».

    Never seconds: the text does not tick, because a clock rewriting itself every
    second is moving content nobody can stop (WCAG 2.2.2), and the fixed end time
    is printed beside it. A zero second unit is dropped, since «۳ روز و ۰ ساعت»
    reads as a bug. `static/js/countdown.js` writes the same text when a tab
    comes back, so the two must change together.
    """
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, minutes = rest // 3600, rest % 3600 // 60
    if days:
        return _count(days, "روز") + (f" و {_count(hours, 'ساعت')}" if hours else "")
    if hours:
        return _count(hours, "ساعت") + (f" و {_count(minutes, 'دقیقه')}" if minutes else "")
    return _count(minutes, "دقیقه") if minutes else f"کمتر از یک{NBSP}دقیقه"


def format_deadline(value: datetime) -> str:
    """When a discount ends, in Tehran time: «۱۱ آبان، ساعت ۲۳:۳۹»."""
    local = timezone.localtime(value)
    day = to_jalali(local)
    month = JALALI_MONTHS[day.month - 1]
    return to_persian_digits(f"{day.day}{NBSP}{month}، ساعت{NBSP}{local:%H:%M}")


_ONES = ("", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه")
_TEENS = ("ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده", "هجده", "نوزده")
_TENS = ("", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود")
_HUNDREDS = ("", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد")
_SCALES = ("", "هزار", "میلیون", "میلیارد")


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    tens, ones = divmod(rest, 10)
    if tens == 1:
        parts = [_HUNDREDS[hundreds], _TEENS[ones]]
    else:
        parts = [_HUNDREDS[hundreds], _TENS[tens], _ONES[ones]]
    return " و ".join(part for part in parts if part)


def toman_in_words(amount: Decimal | int) -> str:
    """An amount as an invoice writes it out: «یک میلیون و صد و هشتاد هزار تومان».

    «هزار» stands without «یک», as it is said; a million keeps it.
    """
    n = int(amount)
    if not 0 < n < 1000 ** len(_SCALES):
        raise ValueError(f"no words for {n} toman")
    groups = []
    for scale in _SCALES:
        n, group = divmod(n, 1000)
        if group:
            words = "" if (group == 1 and scale == "هزار") else _below_thousand(group)
            groups.append(f"{words} {scale}".strip())
    return " و ".join(reversed(groups)) + " تومان"


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

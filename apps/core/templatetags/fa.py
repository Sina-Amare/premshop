"""Template filters for Persian display. Presentation only — no logic here."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django import template

from apps.core import formatting

register = template.Library()


@register.filter(name="toman")
def toman(value: Decimal | int | None) -> str:
    return "" if value is None else formatting.format_toman(value)


@register.filter(name="toman_plain")
def toman_plain(value: Decimal | int | None) -> str:
    return "" if value is None else formatting.format_toman(value, with_unit=False)


@register.filter(name="fa_digits")
def fa_digits(value: object) -> str:
    return formatting.to_persian_digits(value)


@register.filter(name="jalali")
def jalali(value: datetime | date | None) -> str:
    return formatting.format_jalali(value)


@register.filter(name="jalali_numeric")
def jalali_numeric(value: datetime | date | None) -> str:
    return formatting.format_jalali(value, with_month_name=False)

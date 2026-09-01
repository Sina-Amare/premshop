"""Template filters for Persian display. Presentation only — no logic here."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

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


@register.filter(name="price_html")
def price_html(value: Decimal | int | None) -> str:
    """Render the price component: numeral and currency word set separately.

    They differ in family, size and weight by design, so they cannot be one
    string. Zero renders «رایگان» with no currency word.
    """
    if value is None:
        return ""
    if int(value) == 0:
        # Constant markup, no interpolation — nothing to escape.
        return mark_safe(
            '<span class="price"><span class="price__num">رایگان</span></span>'
        )  # noqa: S308
    return format_html(
        '<span class="price"><span class="price__num">{}</span>'
        '<span class="price__cur">تومان</span></span>',
        formatting.format_toman(value, with_unit=False),
    )


@register.filter(name="jalali")
def jalali(value: datetime | date | None) -> str:
    return formatting.format_jalali(value)


@register.filter(name="jalali_numeric")
def jalali_numeric(value: datetime | date | None) -> str:
    return formatting.format_jalali(value, with_month_name=False)

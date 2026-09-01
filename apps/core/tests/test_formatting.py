"""Persian formatting helpers — display correctness for prices and dates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.core import formatting


class TestPersianDigits:
    def test_converts_latin_digits(self) -> None:
        assert formatting.to_persian_digits(1234567890) == "۱۲۳۴۵۶۷۸۹۰"

    def test_leaves_non_digits_untouched(self) -> None:
        assert formatting.to_persian_digits("#1041") == "#۱۰۴۱"

    def test_round_trips_back_to_latin(self) -> None:
        assert formatting.to_latin_digits("۰۹۱۲۳۴۵۶۷۸۹") == "09123456789"


class TestFormatToman:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            (999, "۹۹۹ تومان"),
            (1_000, "۱,۰۰۰ تومان"),
            (450_387, "۴۵۰,۳۸۷ تومان"),
            (1_200_000, "۱,۲۰۰,۰۰۰ تومان"),
            (Decimal("1200000"), "۱,۲۰۰,۰۰۰ تومان"),
        ],
    )
    def test_groups_thousands_in_persian(self, amount: Decimal | int, expected: str) -> None:
        assert formatting.format_toman(amount) == expected

    def test_can_omit_the_unit(self) -> None:
        assert formatting.format_toman(1_200_000, with_unit=False) == "۱,۲۰۰,۰۰۰"

    def test_uses_an_ascii_comma_separator(self) -> None:
        # Not U+066C: it renders as a faint high comma in both of our faces and
        # reads as a rendering fault. Iranian shops use the comma (ADR-0016).
        assert "," in formatting.format_toman(1_000)
        assert "٬" not in formatting.format_toman(1_000)

    def test_zero_reads_as_free_not_zero_toman(self) -> None:
        assert formatting.format_toman(0) == "رایگان"
        # ...but the bare numeral is still available for composing.
        assert formatting.format_toman(0, with_unit=False) == "۰"


class TestJalali:
    @pytest.mark.parametrize(
        ("gregorian", "expected"),
        [
            (date(2026, 9, 1), "۱۰ شهریور ۱۴۰۵"),
            (date(2026, 3, 21), "۱ فروردین ۱۴۰۵"),
            (date(2027, 3, 20), "۲۹ اسفند ۱۴۰۵"),
        ],
    )
    def test_known_dates_convert(self, gregorian: date, expected: str) -> None:
        assert formatting.format_jalali(gregorian) == expected

    def test_numeric_form(self) -> None:
        assert formatting.format_jalali(date(2026, 9, 1), with_month_name=False) == "۱۴۰۵/۰۶/۱۰"

    def test_none_renders_empty(self) -> None:
        assert formatting.format_jalali(None) == ""

    def test_aware_datetime_uses_tehran_local_date(self, settings) -> None:
        # 22:00 UTC is already the next day in Tehran (+03:30) — the displayed
        # date must follow Tehran, not UTC, or SLA dates read a day early.
        settings.TIME_ZONE = "Asia/Tehran"
        utc_evening = datetime(2026, 9, 1, 22, 0, tzinfo=ZoneInfo("UTC"))
        assert formatting.format_jalali(utc_evening) == "۱۱ شهریور ۱۴۰۵"

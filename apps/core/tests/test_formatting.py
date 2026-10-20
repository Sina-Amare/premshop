"""Persian formatting helpers — display correctness for prices and dates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings

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


NB = "\u00a0"


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, f"کمتر از یک{NB}دقیقه"),
        (59, f"کمتر از یک{NB}دقیقه"),
        (60, f"۱{NB}دقیقه"),
        (3600, f"۱{NB}ساعت"),
        (13140, f"۳{NB}ساعت و ۳۹{NB}دقیقه"),
        (86399, f"۲۳{NB}ساعت و ۵۹{NB}دقیقه"),
        (86400, f"۱{NB}روز"),
        (90000, f"۱{NB}روز و ۱{NB}ساعت"),
        (1202400, f"۱۳{NB}روز و ۲۲{NB}ساعت"),
        (-5, f"کمتر از یک{NB}دقیقه"),
    ],
)
def test_a_countdown_reads_at_the_granularity_someone_can_act_on(seconds, expected):
    """Days and hours, under a day hours and minutes, never seconds: the text
    does not tick. A number and its unit never break apart, and a zero second
    unit is dropped. Negative seconds floor at zero."""
    assert formatting.format_countdown(seconds) == expected


def test_the_countdown_script_writes_what_the_server_writes():
    """countdown.js rewrites the badge when a tab comes back; a second
    formatter that drifted would change the text under the reader."""
    script = (Path(settings.BASE_DIR) / "static" / "js" / "countdown.js").read_text("utf-8")
    for word in ("روز", "ساعت", "دقیقه", "کمتر از یک"):
        assert f"'{word}'" in script or f"'{word}' + NB" in script, word
    assert "NB = '\u00a0'" in script


def test_a_deadline_reads_as_a_day_and_a_clock_time_in_tehran(settings):
    settings.TIME_ZONE = "Asia/Tehran"
    end = datetime(2026, 11, 2, 20, 9, 41, tzinfo=ZoneInfo("UTC"))
    assert formatting.format_deadline(end) == f"۱۱{NB}آبان، ساعت{NB}۲۳:۳۹"


@pytest.mark.parametrize(
    "amount, expected",
    [
        (1, "یک تومان"),
        (19, "نوزده تومان"),
        (21, "بیست و یک تومان"),
        (110, "صد و ده تومان"),
        (1000, "هزار تومان"),
        (1500, "هزار و پانصد تومان"),
        (190000, "صد و نود هزار تومان"),
        (Decimal("1180000"), "یک میلیون و صد و هشتاد هزار تومان"),
        (1690000, "یک میلیون و ششصد و نود هزار تومان"),
        (1000001, "یک میلیون و یک تومان"),
        (12345678, "دوازده میلیون و سیصد و چهل و پنج هزار و ششصد و هفتاد و هشت تومان"),
        (2000000000, "دو میلیارد تومان"),
    ],
)
def test_an_amount_reads_in_words_as_an_invoice_writes_it(amount, expected):
    assert formatting.toman_in_words(amount) == expected


@pytest.mark.parametrize("amount", [0, -1, 1000**4])
def test_an_amount_with_no_words_is_refused_rather_than_misread(amount):
    with pytest.raises(ValueError):
        formatting.toman_in_words(amount)

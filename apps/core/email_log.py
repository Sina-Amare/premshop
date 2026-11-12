"""Mail accounting commits independently of the caller's authentication transaction."""

import calendar
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)
PROTECTED = {"otp_code", "telegram_changed"}
COUNTED = ["in_flight", "accepted", "uncertain"]


class MailQuotaExceeded(Exception):
    message = _("ارسال ایمیل فعلاً به سقف مجاز رسیده است. بعداً دوباره امتحان کنید.")


def fingerprint(address: str) -> str:
    return salted_hmac("mail-recipient", address.strip().lower(), algorithm="sha256").hexdigest()


@contextmanager
def _cursor() -> Iterator[Any]:
    independent = connection.copy()
    independent.settings_dict["OPTIONS"]["connect_timeout"] = 3
    try:
        independent.ensure_connection()
        independent.set_autocommit(True)
        with independent.cursor() as cursor:
            cursor.execute("SET statement_timeout = 2000")
            yield cursor
    finally:
        independent.close()


def _month(now: datetime) -> tuple[datetime, datetime, int]:
    anchor = date.fromisoformat(settings.EMAIL_QUOTA_RESET_DATE)
    starting_usage = int(settings.EMAIL_QUOTA_STARTING_USAGE)
    if anchor > now.date() or not 0 <= starting_usage <= 1000:
        raise ValueError("invalid mail allowance baseline")

    def boundary(month: int) -> datetime:
        year, zero_month = divmod(month, 12)
        number = zero_month + 1
        day = min(anchor.day, calendar.monthrange(year, number)[1])
        return datetime(year, number, day, tzinfo=UTC)

    month = now.year * 12 + now.month - 1
    if boundary(month) > now:
        month -= 1
    start, end = boundary(month), boundary(month + 1)
    baseline = starting_usage if start.date() == anchor else 0
    return start, end, baseline


def _usage(cursor: Any, now: datetime) -> dict[str, Any]:
    start, end, baseline = _month(now)
    daily_start = now - timedelta(hours=24)
    cursor.execute(
        "SELECT COUNT(*) FILTER (WHERE created_at > %s), "
        "COUNT(*) FILTER (WHERE created_at >= %s), "
        "MIN(created_at) FILTER (WHERE created_at > %s) "
        "FROM core_mailattempt WHERE status = ANY(%s) AND created_at >= %s",
        [daily_start, start, daily_start, COUNTED, min(start, daily_start)],
    )
    daily, monthly, first_daily = cursor.fetchone()
    return {
        "daily": daily,
        "monthly": monthly + baseline,
        "daily_reset": first_daily + timedelta(hours=24) if first_daily else now,
        "monthly_reset": end,
    }


def usage() -> dict[str, Any]:
    with _cursor() as cursor:
        return _usage(cursor, timezone.now())


def reserve(name: str, recipients: list[str]) -> list[int]:
    now = timezone.now()
    ids = []
    with _cursor() as cursor:
        for address in recipients:
            cursor.execute(
                "INSERT INTO core_mailattempt "
                "(template, recipient_hash, created_at, status, error_type) "
                "VALUES (%s, %s, %s, 'in_flight', '') RETURNING id",
                [name, fingerprint(address), now],
            )
            ids.append(cursor.fetchone()[0])
        # shortcut: concurrent senders can overshoot, use atomic reservations if volume requires them.
        try:
            totals = _usage(cursor, now)
        except Exception as error:  # noqa: BLE001 — a bad baseline must not hide security sends
            accounting_error(error)
            if name in PROTECTED:
                return ids
            cursor.execute(
                "UPDATE core_mailattempt SET status = 'blocked', completed_at = %s "
                "WHERE id = ANY(%s)",
                [timezone.now(), ids],
            )
            raise MailQuotaExceeded from None
        daily, monthly = (200, 1000) if name in PROTECTED else (180, 900)
        if totals["daily"] > daily or totals["monthly"] > monthly:
            cursor.execute(
                "UPDATE core_mailattempt SET status = 'blocked', completed_at = %s "
                "WHERE id = ANY(%s)",
                [timezone.now(), ids],
            )
            raise MailQuotaExceeded
    return ids


def finish(ids: list[int], status: str, error: Exception | None = None) -> None:
    if not ids:
        return
    code = getattr(error, "smtp_code", None)
    with _cursor() as cursor:
        cursor.execute(
            "UPDATE core_mailattempt SET status = %s, completed_at = %s, "
            "error_type = %s, smtp_code = %s WHERE id = ANY(%s)",
            [
                status,
                timezone.now(),
                type(error).__name__[:64] if error else "",
                code if isinstance(code, int) and 100 <= code <= 599 else None,
                ids,
            ],
        )


def accounting_error(error: Exception) -> None:
    logger.error("mail accounting unavailable: %s", type(error).__name__)

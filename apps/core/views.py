"""Core views: the health endpoint and the S1 style-guide page."""

from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache


@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    """Report whether the app can reach its dependencies.

    Monitors poll this; a non-200 means "do not send traffic here". Checks
    grow as dependencies arrive — Redis with S2, the Celery beat heartbeat
    with S6 — so this never claims to verify something that isn't wired yet.
    """
    checks: dict[str, str] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — the reason is reported, not raised
        checks["database"] = f"error: {type(exc).__name__}"

    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )


def styleguide(request: HttpRequest) -> HttpResponse:
    """The S1 demo page: the design system rendered, so it can be reviewed live."""
    return render(
        request,
        "styleguide.html",
        {
            "sample_price": Decimal("1200000"),
            "sample_was": Decimal("1450000"),
            "zero_price": Decimal("0"),
            "unique_amount": Decimal("890387"),
            "now": timezone.now(),
            "table_rows": [
                {
                    "product": "اشتراک یک‌ماهه Claude Pro",
                    "duration": 30,
                    "price": Decimal("1200000"),
                    "status": "تحویل شد",
                    "kind": "ok",
                },
                {
                    "product": "گیفت‌کارت ۱۰ دلاری",
                    "duration": 0,
                    "price": Decimal("890000"),
                    "status": "در صف انجام",
                    "kind": "accent",
                },
                {
                    "product": "اشتراک سه‌ماهه ابزار توسعه",
                    "duration": 90,
                    "price": Decimal("3150000"),
                    "status": "۳ ساعت تا مهلت",
                    "kind": "time",
                },
            ],
            "swatches": [
                {"name": "زمینه", "value": "#FAFAF9"},
                {"name": "متن", "value": "#1C1917"},
                {"name": "متن کم‌رنگ", "value": "#78716C"},
                {"name": "خط", "value": "#E7E5E4"},
                {"name": "لهجه", "value": "#0F766E"},
                {"name": "موفق", "value": "#16A34A"},
                {"name": "زمان", "value": "#D97706"},
                {"name": "خطا", "value": "#DC2626"},
            ],
        },
    )

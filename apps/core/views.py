"""Core views: the health endpoint and the S1 style-guide page."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from apps.core.email import render_email


@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    """Report whether the app can reach its dependencies.

    Monitors poll this; a non-200 means "do not send traffic here". Checks grow
    as dependencies arrive, so this never claims to verify something that is not
    wired yet. The cache check earns its place: login codes and every rate-limit
    counter live there, so Redis being down means authentication is down — and
    without this line the endpoint reported "ok" while nobody could log in.
    """
    checks: dict[str, str] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — the reason is reported, not raised
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        from django.core.cache import cache

        cache.set("healthz", "1", timeout=10)
        if cache.get("healthz") != "1":
            raise RuntimeError("cache did not return what it was given")
        checks["cache"] = "ok"
    except Exception as exc:  # noqa: BLE001 — the reason is reported, not raised
        checks["cache"] = f"error: {type(exc).__name__}"

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


# Sample context so every email template can be reviewed in a browser without
# sending anything. Values are realistic on purpose: a preview filled with "lorem"
# or "123" hides exactly the problems worth catching — long product names wrapping,
# Persian digits in a Latin-shaped order number, a price that outgrows its column.
EMAIL_PREVIEWS: dict[str, dict[str, object]] = {
    "otp_code": {
        "code": "418305",
        "ttl_minutes": 10,
        "login_url": "https://premshop.ir/enter/preview-token",
    },
    "item_delivered": {
        "order_number": "PS-1405-0217",
        "product_name": "اشتراک Claude Pro — یک‌ماهه",
        "amount": Decimal("1450000"),
        "delivery_url": "https://premshop.ir/d/preview-token",
        "link_ttl_hours": 72,  # ADR-0008 settles this; the first draft invented 48
        "order_url": "https://premshop.ir/orders/PS-1405-0217",
    },
}


@never_cache
def email_preview(request: HttpRequest, name: str = "") -> HttpResponse:
    """Render an email template in the browser. Development only.

    Email is the one surface that cannot be checked by reloading a page, so this
    closes the loop: change a template, refresh, look. Add ?part=text to read the
    plain-text half, which is what spam filters and screen readers see.

    Gated on DEBUG rather than on staff status: it needs no database, and a
    preview route that exists in production is one more thing to get wrong.
    """
    if not settings.DEBUG:
        raise Http404("Email previews are development-only.")

    if name not in EMAIL_PREVIEWS:
        items = "".join(
            f'<li><a href="/dev/emails/{key}/">{key}</a> '
            f'· <a href="/dev/emails/{key}/?part=text">text</a></li>'
            for key in EMAIL_PREVIEWS
        )
        return HttpResponse(f"<ul>{items}</ul>")

    subject, text, html = render_email(name, EMAIL_PREVIEWS[name])
    if request.GET.get("part") == "text":
        return HttpResponse(
            f"Subject: {subject}\n\n{text}", content_type="text/plain; charset=utf-8"
        )
    return HttpResponse(html)

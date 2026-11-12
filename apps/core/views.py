"""Core views: the health endpoint and the S1 style-guide page."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

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
        {
            "status": "ok" if healthy else "degraded",
            "checks": checks,
            "commit": settings.GIT_COMMIT,
        },
        status=200 if healthy else 503,
    )


# The palette, read out of the stylesheet that actually paints the site. A
# second copy of these hex values is a second thing to forget: the old hard-coded
# list went on describing a warm-stone canvas for a month after the canvas turned
# white, and the page whose job is documenting the design system was the last
# place anyone looked.
TOKEN_NAMES = [
    ("--color-ground", "زمینه"),
    ("--color-deep", "زمینه‌ی تیره"),
    ("--color-ink", "متن"),
    ("--color-secondary", "متن دوم"),
    ("--color-muted", "متن کم‌رنگ"),
    ("--color-fill-subtle", "سطح کم‌رنگ"),
    ("--color-line", "خط"),
    ("--color-line-strong", "خط پررنگ"),
    ("--color-accent", "لهجه"),
    ("--color-ok", "موفق"),
    ("--color-time", "زمان"),
    ("--color-danger", "خطا"),
]


def design_tokens() -> list[dict[str, str]]:
    source = (Path(settings.BASE_DIR) / "static" / "css" / "input.css").read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for line in source.splitlines():
        name, _, rest = line.strip().partition(":")
        if name.startswith("--color-") and rest.strip().startswith("#"):
            found[name] = rest.split(";")[0].strip()
    return [
        {"name": label, "value": found[token]} for token, label in TOKEN_NAMES if token in found
    ]


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
                    "product": "اشتراک یک‌ماهه‌ی Claude Pro",
                    "duration": 30,
                    "price": Decimal("1200000"),
                    "status": "تحویل‌شده",
                    "kind": "ok",
                    "icon": "check",
                },
                {
                    "product": "گیفت کارت ۱۰ دلاری",
                    "duration": 0,
                    "price": Decimal("890000"),
                    "status": "در حال آماده‌سازی",
                    "kind": "progress",
                    "icon": "progress",
                },
                {
                    "product": "اشتراک سه‌ماهه‌ی ابزار توسعه",
                    "duration": 90,
                    "price": Decimal("3150000"),
                    "status": "۳ ساعت تا مهلت",
                    "kind": "time",
                    "icon": "clock",
                },
            ],
            # Read from the stylesheet, never retyped. This list described the
            # warm-stone palette for a month after the canvas turned white,
            # because nothing compared the two — see test_the_styleguide_
            # swatches_match_the_stylesheet.
            "swatches": design_tokens(),
        },
    )


# Sample context so every email template can be reviewed in a browser without
# sending anything. Values are realistic on purpose: a preview filled with "lorem"
# or "123" hides exactly the problems worth catching — long product names wrapping,
# Persian digits in a Latin-shaped order number, a price that outgrows its column.
EMAIL_PREVIEWS: dict[str, dict[str, object]] = {
    "purchase_confirmation": {
        "order_number": 14050217,
        "created_at": datetime(2026, 11, 3, 11, 2, tzinfo=UTC),
        "order_url": "https://premshop.ir/account/orders/14050217/",
        "lines": [
            {
                "facts": {
                    "name": "اشتراک نمونه Example Pro",
                    "plan_title": "یک ماهه",
                    "duration_days": 30,
                    "delivery_note": "تحویل تا ۴۸ ساعت پس از تأیید پرداخت",
                    "usage_notes": "شرایط استفاده‌ی ثبت‌شده با سفارش",
                    "refund_note": "شرایط بازگشت وجه ثبت‌شده با سفارش",
                    "full_description": "توضیح کامل محصول زمان خرید",
                    "specs": [{"title": "مدت", "value": "۳۰ روز"}],
                    "sections": [{"title": "شرایط محصول", "body": "متن کامل شرایط محصول"}],
                },
                "quantity": 2,
                "price": Decimal("200000"),
                "amount": Decimal("400000"),
                "warranty": "طبق شرایط محصول",
                "delivery": "روی حساب شما",
            }
        ],
        "policies": [
            {"title": "قوانین خرید", "body": "متن قوانین ثبت‌شده با سفارش"},
            {"title": "بازگشت وجه", "body": "متن شرایط بازگشت وجه"},
            {"title": "تماس", "body": "راه‌های تماس ثبت‌شده با سفارش"},
        ],
        "subtotal": Decimal("400000"),
        "discount_amount": Decimal("0"),
        "total_amount": Decimal("400000"),
    },
    "payment_confirmed": {
        "order_number": 14050217,
        "order_url": "https://premshop.ir/account/orders/14050217/",
        "amount": Decimal("1450000"),
        "pill_state": "queued",
    },
    # Each notice with the state it reports, named as apps/orders/status.py names it.
    **{
        name: {
            "order_number": 14050217,
            "order_url": "https://premshop.ir/account/orders/14050217/",
            "product_name": "اشتراک نمونه Example Pro",
            "plan_title": "یک ماهه",
            "amount": Decimal("1450000"),
            "due_at": datetime(2026, 11, 11, 11, 2, tzinfo=UTC),
            "pill_state": state,
        }
        for name, state in (
            ("input_requested", "awaiting_input"),
            ("delivery_completed", "delivered"),
            ("replacement_rejected", "delivered"),
            ("supply_delayed", "queued"),
            ("item_cancelled", "refund_owed"),
            ("item_refunded", "refunded"),
        )
    },
    "item_delivered": {
        "order_number": "PS-1405-0217",
        # A ready account: Claude Pro is activated on the customer's own and
        # has no login details to deliver, which this mail says it has.
        "product_name": "اکانت یک‌ماهه‌ی Netflix",
        "amount": Decimal("1450000"),
        "delivery_url": "https://premshop.ir/d/preview-token",
        "link_ttl_hours": 72,  # ADR-0008 settles this; the first draft invented 48
        "order_url": "https://premshop.ir/orders/PS-1405-0217",
    },
    "telegram_changed": {
        "state": "linked",
        "telegram": "@premshop_customer",
        "changed_at": datetime(2026, 9, 18, 11, 2, tzinfo=UTC),
        "account_url": "https://premshop.ir/account/",
    },
    "sms_budget": {
        "sent": 100,
        "ceiling": 300,
        # 11:02 UTC is 14:32 in Tehran — the template must render local time.
        "at": datetime(2026, 11, 12, 11, 2, tzinfo=UTC),
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

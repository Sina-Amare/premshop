import re
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.core import ratelimit
from apps.core.formatting import format_jalali, format_toman, to_persian_digits
from apps.orders import status as order_status
from apps.orders.models import OrderSupportIdentifier
from apps.orders.selectors import (
    customer_orders,
    customer_summary,
    customer_timeline,
    order_for_tracking,
    order_lead,
)
from apps.orders.services import cancel_unpaid_order, replace_support_token

ORDERS_PER_PAGE = 20
_TRACKING_IP = ratelimit.Limit("tracking:ip", times=30, seconds=60)
_TRACKING_TOKEN = ratelimit.Limit("tracking:token", times=60, seconds=60)
INVOICE_NOTICE = "این فاکتور رسید پرداخت نیست."


@never_cache
@login_required
@require_GET
def order_list(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    groups: dict[str, list[Any]] = {key: [] for key in order_status.TABS}
    for order in customer_orders(cast(User, request.user)):
        groups[order_status.tab_of(order_lead(order, now))].append(order)
    tab = request.GET.get("tab", "")
    if tab not in groups:
        tab = next((key for key, orders in groups.items() if orders), "current")
    page = Paginator(groups[tab], ORDERS_PER_PAGE).get_page(request.GET.get("page"))
    return render(
        request,
        "orders/list.html",
        {
            "page": page,
            "summaries": [customer_summary(order) for order in page],
            "tab": tab,
            "tabs": [
                {"key": key, "label": label, "count": len(groups[key])}
                for key, (label, _) in order_status.TABS.items()
            ],
            "empty": order_status.TABS[tab][1],
            "has_orders": any(groups.values()),
            "section": "orders",
        },
    )


def _owned_summary(request: HttpRequest, order_number: int) -> dict[str, Any]:
    order = get_object_or_404(customer_orders(cast(User, request.user)), order_number=order_number)
    return customer_summary(order)


def _again_url(summary: dict[str, Any]) -> str:
    """Where buying again starts: the first product still on sale, else the shop. Only the
    closed cards offer it."""
    if summary["card"]["state"] not in ("expired", "cancelled"):
        return ""
    ids = [
        line["product"].pk
        for group in summary["groups"]
        for line in group["lines"]
        if line["product"] is not None
    ]
    on_sale = {product.pk: product for product in Product.objects.public().filter(pk__in=ids)}
    for pk in ids:
        if pk in on_sale:
            return on_sale[pk].get_absolute_url()
    return reverse("home")


@never_cache
@login_required
@require_GET
def detail(request: HttpRequest, order_number: int) -> HttpResponse:
    return _render_detail(request, _owned_summary(request, order_number))


def _render_detail(
    request: HttpRequest, summary: dict[str, Any], *, error: str = "", status: int = 200
) -> HttpResponse:
    try:
        support_identifier = OrderSupportIdentifier.objects.filter(order=summary["order"]).first()
    except ImproperlyConfigured:
        support_identifier = None
    response = render(
        request,
        "orders/detail.html",
        {
            **summary,
            "timeline": customer_timeline(summary["order"]),
            "section": "orders",
            "error": error,
            "support_identifier": support_identifier,
            "again_url": _again_url(summary),
            # Order news goes by email or Telegram; with neither, this page is the news.
            "reachable": bool(
                summary["order"].placement_email
                or summary["order"].user.email
                or summary["order"].user.telegram_id
            ),
        },
        status=status,
    )
    response["Content-Encoding"] = "identity"
    response["Referrer-Policy"] = "same-origin"
    return response


@sensitive_post_parameters("expected_support_token")
@never_cache
@login_required
@require_POST
def replace_support(request: HttpRequest, order_number: int) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    order = summary["order"]
    if request.POST.get("confirm") != "yes":
        return _render_detail(
            request, summary, error="ساخت شناسه‌ی جدید را تأیید کنید.", status=400
        )
    try:
        replace_support_token(
            order_id=order.pk,
            actor=cast(User, request.user),
            expected_support_token=request.POST.get("expected_support_token", ""),
        )
    except ValidationError as invalid:
        return _render_detail(request, summary, error=" ".join(invalid.messages), status=400)
    except ImproperlyConfigured, OrderSupportIdentifier.DoesNotExist:
        return _render_detail(request, summary, error="شناسه‌ی پشتیبانی در دسترس نیست.", status=503)
    messages.success(request, "شناسه‌ی پشتیبانی جایگزین شد.")
    return redirect("order-detail", order_number=order.order_number)


@never_cache
@login_required
@require_POST
def cancel(request: HttpRequest, order_number: int) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    order = summary["order"]
    error = "برای لغو سفارش، دکمه‌ی «بله، لغو سفارش» را بزنید."
    if request.POST.get("confirm") == "yes":
        try:
            cancel_unpaid_order(order_id=order.pk, actor=cast(User, request.user))
        except ValidationError as invalid:
            error = " ".join(invalid.messages)
        else:
            # The page itself now says it was cancelled; a flash above it would say it twice.
            return redirect("order-detail", order_number=order.order_number)
    return _render_detail(request, summary, error=error, status=400)


@never_cache
@login_required
@require_GET
def invoice(request: HttpRequest, order_number: int) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    order = summary["order"]
    money = summary["money"]
    # Until a payment is verified the sheet is no receipt; afterwards it says when it was paid.
    notice = "" if money["paid"] is not None else INVOICE_NOTICE
    paid = (
        f"پرداخت‌شده در {format_jalali(money['paid_at'])}: {format_toman(money['paid'])}"
        if money["paid"] is not None and money["paid_at"]
        else ""
    )
    data = {
        "number": to_persian_digits(order.order_number),
        "date": format_jalali(order.created_at),
        "status": summary["status"],
        "lines": [
            {
                "name": line["facts"].get("name", ""),
                "plan": line["facts"].get("plan_title", ""),
                "quantity": to_persian_digits(line["quantity"]),
                "status": line["status_label"],
                "price": format_toman(line["price"]),
                "amount": format_toman(line["amount"]),
            }
            for group in summary["groups"]
            for line in group["lines"]
        ],
        "subtotal": format_toman(order.subtotal),
        "discount": format_toman(order.discount_amount, with_unit=False) + " تومان",
        "total": format_toman(order.total_amount),
        "notice": notice,
        "paid": paid,
    }
    response = render(
        None, "orders/invoice.html", {**summary, "invoice_data": data, "notice": notice}
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


@require_GET
def tracking(request: HttpRequest, token: str) -> HttpResponse:
    subject = salted_hmac("orders.tracking.limit", token).hexdigest()
    if not ratelimit.hit(_TRACKING_IP, ratelimit.client_ip(request)) or not ratelimit.hit(
        _TRACKING_TOKEN, subject
    ):
        return render(None, "orders/tracking_error.html", status=429)
    facts = order_for_tracking(token) if re.fullmatch(r"[A-Za-z0-9_-]{22,32}", token) else None
    if facts is None:
        return render(None, "orders/tracking_error.html", status=404)
    return render(None, "orders/tracking.html", {"tracking": facts})

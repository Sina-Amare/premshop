import re
from datetime import timedelta
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
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
from apps.core.formatting import format_jalali, format_toman, mask_phone, to_persian_digits
from apps.orders import holiday, proof
from apps.orders import status as order_status
from apps.orders.forms import ReviewForm
from apps.orders.models import (
    Delivery,
    DeliveryField,
    Order,
    OrderItem,
    OrderItemEvent,
    OrderSupportIdentifier,
    Review,
)
from apps.orders.selectors import (
    claim_answer,
    customer_orders,
    customer_summary,
    customer_timeline,
    order_for_tracking,
    order_lead,
)
from apps.orders.services import (
    available_claim_reasons,
    can_submit_customer_input,
    cancel_unpaid_order,
    input_last_day,
    open_customer_claim,
    replace_support_token,
    reveal_delivery,
    submit_customer_input,
)

ORDERS_PER_PAGE = 20
_TRACKING_IP = ratelimit.Limit("tracking:ip", times=30, seconds=60)
_TRACKING_TOKEN = ratelimit.Limit("tracking:token", times=60, seconds=60)
INVOICE_NOTICE = "این فاکتور رسید پرداخت نیست."
# The code steps redirect back to the delivery card, so their notices are shown there.
DELIVERY_NOTICE = "order-delivery"
DELIVERY_SENT = {
    "email": "کد را به ایمیل این سفارش فرستادیم.",
    "sms": "کد را برایتان پیامک کردیم.",
}


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
    request: HttpRequest,
    summary: dict[str, Any],
    *,
    error: str = "",
    delivery_error: str = "",
    status: int = 200,
    review_error: tuple[int, str, Any] | None = None,
) -> HttpResponse:
    try:
        support_identifier = OrderSupportIdentifier.objects.filter(order=summary["order"]).first()
    except ImproperlyConfigured:
        support_identifier = None
    items = list(summary["order"].items.all())
    is_holiday = holiday.current() is not None
    holiday_delivery = is_holiday and any(
        item.status == OrderItem.Status.QUEUED and item.sla_paused_at for item in items
    )
    input_requests = [
        {
            "item": item,
            "can_submit": can_submit_customer_input(item),
            "last_day": (
                input_last_day(item.input_requested_at) if item.input_requested_at else None
            ),
        }
        for item in items
        if item.status == OrderItem.Status.AWAITING_INPUT
    ]
    response = render(
        request,
        "orders/detail.html",
        {
            **summary,
            **_delivery_context(request, summary["order"]),
            "timeline": customer_timeline(summary["order"]),
            "section": "orders",
            "error": error,
            "delivery_error": delivery_error,
            "input_requests": input_requests,
            "review_items": _review_items(summary["order"], review_error),
            "has_site_input": any(row["can_submit"] for row in input_requests),
            "is_holiday": is_holiday,
            "holiday_delivery": (
                "زمان تعطیلی جزو مهلت تحویل حساب نمی‌شود." if holiday_delivery else ""
            ),
            "support_identifier": support_identifier,
            "again_url": _again_url(summary),
            "telegram_available": bool(settings.TELEGRAM_BOT_TOKEN),
            # Order news goes by email or Telegram; with neither, this page is the news.
            "reachable": bool(
                summary["order"].placement_email
                or summary["order"].user.email
                or (settings.TELEGRAM_BOT_TOKEN and summary["order"].user.telegram_id)
            ),
        },
        status=status,
    )
    response["Content-Encoding"] = "identity"
    response["Referrer-Policy"] = "same-origin"
    return response


def _review_items(order: Order, error: tuple[int, str, Any] | None) -> list[dict[str, Any]]:
    items = [item for item in order.items.all() if item.delivered_at is not None]
    reviews = {
        review.order_item_id: review
        for review in Review.objects.filter(order_item_id__in=[item.pk for item in items])
    }
    return [
        {
            "item": item,
            "review": reviews.get(item.pk),
            "error": error[1] if error and error[0] == item.pk else "",
            "submitted": error[2] if error and error[0] == item.pk else {},
        }
        for item in items
    ]


def _delivery_context(request: HttpRequest, order: Any) -> dict[str, Any]:
    sets = {
        delivery.order_item_id: delivery
        for delivery in Delivery.objects.filter(order_item__order=order)
        .order_by("order_item_id", "-pk")
        .distinct("order_item_id")
        .defer("note")
        .prefetch_related(
            Prefetch(
                "fields", queryset=DeliveryField.objects.only("delivery_id", "position", "title")
            )
        )
    }
    rows = [
        {
            "item": item,
            "delivery": sets.get(item.pk),
            "available": item.status
            in (OrderItem.Status.DELIVERED, OrderItem.Status.REPLACEMENT_REQUESTED),
        }
        for item in order.items.all()
        if item.delivered_at
    ]
    for row in rows:
        item, delivery = row["item"], row["delivery"]
        available = available_claim_reasons(
            item, current_delivery_at=delivery.created_at if delivery else None
        )
        row["claim_choices"] = [
            (value, OrderItemEvent.ClaimReason(value).label) for value in available
        ]
        row["claim_deadline"] = (
            delivery.created_at + timedelta(hours=48)
            if delivery and any(value != OrderItemEvent.ClaimReason.STOPPED for value in available)
            else None
        )
        row["warranty_end"] = (
            order_status.warranty_last_day(item)
            if OrderItemEvent.ClaimReason.STOPPED in available
            else None
        )
        row["claim_reason"] = (
            OrderItemEvent.ClaimReason(item.claim_reason).label if item.claim_reason else ""
        )
        outcome = (
            "open" if item.status == OrderItem.Status.REPLACEMENT_REQUESTED else claim_answer(item)
        )
        row["claim_pill"] = order_status.claim_pill(outcome) if outcome else None
    email, _ = proof.email_for(order) if rows else ("", 0)
    phone = proof.phone_for(order) if rows else ""
    return {
        "delivery_rows": rows,
        "delivery_ready": any(row["available"] and row["delivery"] for row in rows),
        "delivery_email": (email[:1] + "•••@" + email.partition("@")[2]) if email else "",
        "delivery_phone": mask_phone(phone),
        "delivery_confirmed": (
            proof.is_confirmed(order=order, session_key=request.session.session_key or "")
            if rows
            else False
        ),
    }


@sensitive_post_parameters("code")
@never_cache
@login_required
@require_POST
def delivery_code(
    request: HttpRequest, order_number: int, *, confirm: bool = False
) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    arguments = {
        "order_id": summary["order"].pk,
        "actor": cast(User, request.user),
        "session_key": request.session.session_key or "",
    }
    # The digit boxes send the code by fetch and turn green or red on this answer.
    wants_json = confirm and "application/json" in request.headers.get("Accept", "")
    try:
        if confirm:
            if not proof.confirm_code(**arguments, code=request.POST.get("code", "")):
                raise ValidationError("کد اشتباه است یا اعتبارش تمام شده است.")
        else:
            channel = "sms" if request.POST.get("channel") == "sms" else "email"
            proof.request_code(**arguments, ip=ratelimit.client_ip(request), channel=channel)
            messages.success(request, DELIVERY_SENT[channel], DELIVERY_NOTICE)
    except ValidationError as invalid:
        if wants_json:
            return JsonResponse({"ok": False, "error": " ".join(invalid.messages)}, status=400)
        return _render_detail(
            request, summary, delivery_error=" ".join(invalid.messages), status=400
        )
    card = reverse("order-detail", args=[order_number]) + "#order-delivery"
    return JsonResponse({"ok": True, "next": card}) if wants_json else redirect(card)


@never_cache
@login_required
@require_POST
def delivery_reveal(request: HttpRequest, order_number: int, item_id: int) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    item = next((row for row in summary["order"].items.all() if row.pk == item_id), None)
    if item is None:
        raise Http404
    state = "unavailable"
    try:
        content = reveal_delivery(
            order_id=summary["order"].pk,
            item_id=item_id,
            actor=cast(User, request.user),
            ip=ratelimit.client_ip(request),
            customer_session=request.session.session_key or "",
        )
    except PermissionDenied:
        # The page was opened while the proof held; the owner only needs a new code.
        messages.error(request, proof.LAPSED, DELIVERY_NOTICE)
        return redirect(reverse("order-detail", args=[order_number]) + "#order-delivery")
    except ImproperlyConfigured:
        content, state = None, "unreadable"
    else:
        item.refresh_from_db(fields=["status"])
        if content is None and item.status in (
            OrderItem.Status.DELIVERED,
            OrderItem.Status.REPLACEMENT_REQUESTED,
        ):
            state = "deleted"
    response = render(
        request,
        "orders/delivery.html",
        {
            "order": summary["order"],
            "item": item,
            "content": content,
            "state": state,
            "section": "orders",
        },
    )
    response["Content-Encoding"] = "identity"
    response["Referrer-Policy"] = "no-referrer"
    return response


@sensitive_post_parameters("review_text")
@never_cache
@login_required
@require_POST
def submit_review(request: HttpRequest, order_number: int, item_id: int) -> HttpResponse:
    get_object_or_404(
        OrderItem.objects.only("id"),
        pk=item_id,
        order__order_number=order_number,
        order__user=request.user,
        delivered_at__isnull=False,
    )
    form = ReviewForm(request.POST)
    if not form.is_valid():
        return _render_detail(
            request,
            _owned_summary(request, order_number),
            status=400,
            review_error=(
                item_id,
                " ".join(str(error) for errors in form.errors.values() for error in errors),
                request.POST,
            ),
        )
    _, created = Review.objects.get_or_create(
        order_item_id=item_id,
        defaults={"rating": form.cleaned_data["rating"], "text": form.cleaned_data["review_text"]},
    )
    if not created:
        return _render_detail(
            request,
            _owned_summary(request, order_number),
            status=409,
            review_error=(item_id, "نظرتان برای این خرید قبلاً ثبت شده است.", {}),
        )
    return redirect(reverse("order-detail", args=[order_number]) + "#order-reviews")


@sensitive_post_parameters("customer_input")
@never_cache
@login_required
@require_POST
def provide_input(request: HttpRequest, order_number: int, item_id: int) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    order = summary["order"]
    if not any(item.pk == item_id for item in order.items.all()):
        raise Http404
    try:
        expected = int(request.POST.get("expected_event_id", ""))
    except ValueError:
        expected = 0
    try:
        submit_customer_input(
            order_id=order.pk,
            item_id=item_id,
            actor=cast(User, request.user),
            expected_event_id=expected,
            customer_input=request.POST.get("customer_input", ""),
        )
    except ValidationError as invalid:
        return _render_detail(
            request,
            _owned_summary(request, order_number),
            error=" ".join(invalid.messages),
            status=400,
        )
    return redirect("order-detail", order_number=order.order_number)


@sensitive_post_parameters()
@never_cache
@login_required
@require_POST
def file_claim(request: HttpRequest, order_number: int, item_id: int) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    if not any(item.pk == item_id for item in summary["order"].items.all()):
        raise Http404
    try:
        expected = int(request.POST.get("expected_event_id", ""))
    except ValueError:
        expected = 0
    try:
        open_customer_claim(
            order_id=summary["order"].pk,
            item_id=item_id,
            actor=cast(User, request.user),
            expected_event_id=expected,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as invalid:
        return _render_detail(
            request,
            _owned_summary(request, order_number),
            error=" ".join(invalid.messages),
            status=400,
        )
    return redirect("order-detail", order_number=order_number)


@sensitive_post_parameters("expected_support_token")
@never_cache
@login_required
@require_POST
def replace_support(request: HttpRequest, order_number: int) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    order = summary["order"]
    if request.POST.get("confirm") != "yes":
        return _render_detail(
            request,
            summary,
            error="برای تغییر شناسه، دکمه‌ی «ساخت شناسه‌ی جدید» را بزنید.",
            status=400,
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
    messages.success(request, "شناسه‌ی پشتیبانی جدید آماده است.")
    return redirect("order-detail", order_number=order.order_number)


@never_cache
@login_required
@require_POST
def cancel(request: HttpRequest, order_number: int) -> HttpResponse:
    summary = _owned_summary(request, order_number)
    order = summary["order"]
    error = "برای تأیید، دکمه‌ی «بله، لغو سفارش» را بزنید."
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

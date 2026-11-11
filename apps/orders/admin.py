"""Read-only purchase records in the existing operator panel."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import jdatetime
from django import forms
from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db.models import Count, Exists, F, Max, Min, OuterRef, Prefetch, Q, QuerySet
from django.db.models.fields.json import KeyTextTransform
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from unfold.admin import ModelAdmin, TabularInline
from unfold.views import ChangeList

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.core.formatting import (
    format_countdown,
    format_jalali,
    format_toman,
    to_latin_digits,
    to_persian_digits,
)
from apps.core.ratelimit import client_ip
from apps.orders import status as order_status
from apps.orders.models import (
    CredentialAccessLog,
    DiscountCode,
    DiscountRedemption,
    Order,
    OrderItem,
    OrderItemEvent,
    OrderSupportIdentifier,
    UnpaidExpirySweep,
)
from apps.orders.selectors import grouped_items
from apps.orders.services import (
    DELIVERY_LINES,
    PAID_CANCELLATIONS,
    DeliveryLine,
    cancel_paid_item,
    delay_supply,
    deliver_item,
    delivery_content,
    match_support_token,
    open_claim,
    receive_input,
    refund_amount,
    reject_claim,
    replace_delivery,
    request_input,
    resume_input,
    reveal_customer_input,
    reveal_delivery,
)

# Which product and plan an item row is about, read from the purchase record in
# the same query. An annotation, so no OrderItem is built and the encrypted
# input is never selected.
_ITEM_NAME = KeyTextTransform("name", "order_item__product_snapshot")
_ITEM_PLAN = KeyTextTransform("plan_title", "order_item__product_snapshot")

_QUEUE_STATES = {
    "unpaid": OrderItem.Status.PENDING_PAYMENT,
    "queued": OrderItem.Status.QUEUED,
    "waiting": OrderItem.Status.AWAITING_INPUT,
    "claims": OrderItem.Status.REPLACEMENT_REQUESTED,
}
# What each tab's oldest work is measured by.
_QUEUE_ORDER = {"queued": "due_at", "waiting": "input_requested_at", "claims": "delivered_at"}


def queue_tab(request: HttpRequest) -> str:
    value = request.GET.get("tab", "queued")
    return value if value in {*_QUEUE_STATES, "all"} else "queued"


# The operator's next step, by the state apps/orders/status.py ranks first.
_NEXT_WORDS = {
    "late": _("دیر شده: تحویل بدهید"),
    "queued": _("تحویل بدهید"),
    "replacement": _("درخواست تعویض را بررسی کنید"),
    "refund_owed": _("بازگشت وجه %(owed)s را واریز کنید"),
    "awaiting_input": _("منتظر اطلاعات مشتری"),
    "unpaid": _("منتظر پرداخت مشتری"),
}


def _next_step(
    items: Iterable[OrderItem], unpaid_until: datetime, at: datetime
) -> dict[str, str] | None:
    """The one line above an order's items: every item counts, whatever the tab shows."""
    states, owed = [], Decimal(0)
    for item in items:
        refund = getattr(item, "refund", None)
        state = order_status.item_state(item, refund, unpaid_until, at)
        states.append(state)
        if state == "refund_owed" and refund is not None:
            owed += refund.amount
    first = order_status.operator_next(states)
    if first is None:
        return None
    words = _NEXT_WORDS[first]
    text = words % {"owed": format_toman(owed)} if first == "refund_owed" else str(words)
    return {"text": text, "tone": order_status.STATES[first][1]}


class OrderChangeList(ChangeList):
    def get_ordering(self, request: HttpRequest, queryset: QuerySet[Order]) -> Any:
        if "o" not in request.GET and queue_tab(request) != "all":
            return queryset.query.order_by
        return super().get_ordering(request, queryset)

    def get_filters_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result = super().get_filters_params(params)
        result.pop("tab", None)
        return result


class OrderItemInline(TabularInline):
    model = OrderItem
    fields = (
        "purchase",
        "status_pill",
        "sale_amount",
        "cost_amount",
        "cancel_reason",
        "refund_state",
        "input_reveal",
        "delivery",
    )
    readonly_fields = fields
    extra = 0
    can_delete = False
    classes = ("order-items",)
    show_title = False

    def get_queryset(self, request: HttpRequest) -> QuerySet[OrderItem]:
        return (
            super().get_queryset(request).defer("customer_input").select_related("refund", "order")
        )

    @admin.display(description=_("وضعیت"))
    def status_pill(self, obj: OrderItem) -> str:
        return format_html(
            '<span class="order-status" data-tone="{}" data-state="{}">{}</span>',
            order_status.item_tone(obj),
            obj.status,
            obj.get_status_display(),
        )

    @admin.display(description=_("بازپرداخت"))
    def refund_state(self, obj: OrderItem) -> str:
        refund = getattr(obj, "refund", None)
        if refund is None:
            return "—"
        tone = "attention" if refund.status == refund.Status.OWED else "closed"
        return format_html(
            '<span class="order-status" data-tone="{}">{} · {}</span>',
            tone,
            format_toman(refund.amount),
            refund.get_status_display(),
        )

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.display(description=_("اطلاعات مشتری"))
    def input_reveal(self, obj: OrderItem) -> str:
        if not obj.product_snapshot.get("requires_customer_input"):
            return "—"
        return format_html(
            '<button type="submit" formmethod="post" formaction="{}" '
            'class="order-input-action" data-customer-input-reveal>{}</button>',
            reverse("admin:orders_order_reveal_customer_input", args=[obj.order_id, obj.pk]),
            _("نمایش اطلاعات"),
        )

    @admin.display(description=_("محصول و پلن زمان خرید"))
    def purchase(self, obj: OrderItem) -> str:
        return format_html(
            '<span class="order-purchase"><bdi>{}</bdi><span><bdi>{}</bdi></span></span>',
            obj.product_snapshot["name"],
            obj.product_snapshot["plan_title"],
        )

    @admin.display(description=_("قیمت فروش زمان خرید"))
    def sale_amount(self, obj: OrderItem) -> str:
        return format_toman(obj.price_snapshot)

    @admin.display(description=_("قیمت تأمین زمان خرید"))
    def cost_amount(self, obj: OrderItem) -> str:
        return format_toman(obj.cost_snapshot)

    @admin.display(description=_("تحویل"))
    def delivery(self, obj: OrderItem) -> str:
        if obj.status == OrderItem.Status.QUEUED:
            return format_html(
                '<a class="order-input-action" href="{}">{}</a>',
                reverse("admin:orders_order_deliver", args=[obj.order_id, obj.pk]),
                _("تحویل"),
            )
        if obj.status == OrderItem.Status.AWAITING_INPUT:
            return format_html(
                '<a class="order-input-action" href="{}">{}</a>',
                reverse("admin:orders_order_deliver", args=[obj.order_id, obj.pk]),
                _("لغو و بازپرداخت"),
            )
        if obj.delivered_at is None:
            return "—"
        reveal = format_html(
            '<button type="submit" formmethod="post" formaction="{}" '
            'class="order-input-action" data-delivery-reveal>{}</button>',
            reverse("admin:orders_order_reveal_delivery", args=[obj.order_id, obj.pk]),
            _("نمایش اطلاعات تحویل"),
        )
        if obj.status == OrderItem.Status.DELIVERED:
            claim = _("درخواست تعویض")
        elif obj.status == OrderItem.Status.REPLACEMENT_REQUESTED:
            claim = _("تعویض یا رد درخواست")
        else:
            return reveal
        return format_html(
            '{} <a class="order-input-action" href="{}">{}</a>',
            reveal,
            reverse("admin:orders_order_deliver", args=[obj.order_id, obj.pk]),
            claim,
        )


class DeliveryForm(forms.Form):
    expected_event_id = forms.IntegerField(widget=forms.HiddenInput)
    note = forms.CharField(
        label=_("یادداشت برای مشتری"),
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    expires_on = forms.CharField(
        label=_("پایان اشتراک"),
        required=False,
        max_length=10,
        help_text=_("به تاریخ شمسی مثل ۱۴۰۵/۰۹/۱۵، و خالی برای اشتراک بی‌پایان."),
        widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "numeric", "autocomplete": "off"}),
    )
    actual_cost = forms.DecimalField(
        label=_("قیمت تأمین واقعی (تومان)"),
        max_digits=12,
        decimal_places=0,
        min_value=0,
        widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "numeric"}),
    )

    def __init__(
        self, *args: Any, warranty: str | None = None, replacement: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.warranty = warranty
        if replacement:
            # A replacement adds to what supply already cost; the service sums them.
            self.fields["actual_cost"].label = _("هزینه‌ی تأمین اضافه (تومان)")
        for n in range(DELIVERY_LINES):
            number = to_persian_digits(n + 1)
            self.fields[f"title_{n}"] = forms.CharField(
                label=_("عنوان %(n)s") % {"n": number}, required=False, max_length=100
            )
            self.fields[f"value_{n}"] = forms.CharField(
                label=_("مقدار %(n)s") % {"n": number},
                required=False,
                max_length=2000,
                widget=forms.TextInput(
                    attrs={
                        "dir": "ltr",
                        "autocomplete": "off",
                        "autocapitalize": "off",
                        "spellcheck": "false",
                    }
                ),
            )

    def lines(self) -> list[tuple[forms.BoundField, forms.BoundField]]:
        return [(self[f"title_{n}"], self[f"value_{n}"]) for n in range(DELIVERY_LINES)]

    def clean_expires_on(self) -> date | None:
        text = to_latin_digits(self.cleaned_data["expires_on"]).strip()
        if not text:
            return None
        found = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)
        try:
            if found is None:
                raise ValueError
            return cast(date, jdatetime.date(*map(int, found.groups())).togregorian())
        except ValueError:
            raise ValidationError(_("تاریخ را مثل ۱۴۰۵/۰۹/۱۵ بنویسید.")) from None

    def clean(self) -> dict[str, Any]:
        data = self.cleaned_data
        if not self.errors:
            try:
                data["content"] = delivery_content(
                    [
                        DeliveryLine(data[f"title_{n}"], data[f"value_{n}"])
                        for n in range(DELIVERY_LINES)
                    ],
                    data["note"],
                    data["expires_on"],
                    self.warranty,
                )
            except ValidationError as error:
                self.add_error(None, error)
        return data


def _private(response: HttpResponse, referrer: str = "no-referrer") -> HttpResponse:
    # Deliberately send identity to bypass Django gzip despite RFC 9110's SHOULD NOT.
    response["Content-Encoding"] = "identity"
    response["X-Robots-Tag"] = "noindex, nofollow"
    # A page that posts needs same-origin: under no-referrer the browser sends Origin: null,
    # which Django's CSRF check refuses.
    response["Referrer-Policy"] = referrer
    return response


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    change_form_template = "admin/orders/change_form.html"
    change_list_template = "admin/orders/order/change_list.html"
    list_display = ("number", "customer", "amount", "queue_work", "placed")
    list_per_page = 25
    list_max_show_all = 0
    show_full_result_count = False
    search_fields = ("=order_number", "user__email")
    ordering = ("-created_at", "-pk")
    actions = None
    inlines = (OrderItemInline,)
    readonly_fields = (
        "number",
        "customer",
        "channel",
        "placed",
        "subtotal_amount",
        "discount",
        "amount",
        "queue_work",
    )
    fieldsets = (
        (_("مشخصات سفارش"), {"fields": (("number", "customer"), ("channel", "placed"))}),
        (_("مبلغ‌های سفارش"), {"fields": (("subtotal_amount", "discount", "amount"),)}),
        (_("انجام سفارش"), {"fields": ("queue_work",)}),
    )
    change_form_after_template = "admin/orders/history.html"

    def get_urls(self) -> Any:
        return [
            path(
                "<int:order_id>/support/match/",
                self.admin_site.admin_view(self.support_match_view),
                name="orders_order_support_match",
            ),
            path(
                "<int:order_id>/items/<int:item_id>/input/<str:action>/",
                self.admin_site.admin_view(self.input_action_view),
                name="orders_order_input_action",
            ),
            path(
                "<int:order_id>/items/<int:item_id>/customer-input/reveal/",
                self.admin_site.admin_view(self.reveal_view),
                {"target": "customer_input"},
                name="orders_order_reveal_customer_input",
            ),
            path(
                "<int:order_id>/items/<int:item_id>/delivery/reveal/",
                self.admin_site.admin_view(self.reveal_view),
                {"target": "delivery"},
                name="orders_order_reveal_delivery",
            ),
            path(
                "<int:order_id>/items/<int:item_id>/deliver/",
                self.admin_site.admin_view(self.deliver_view),
                name="orders_order_deliver",
            ),
            path(
                "<int:order_id>/items/<int:item_id>/action/<str:action>/",
                self.admin_site.admin_view(self.item_action_view),
                name="orders_order_item_action",
            ),
        ] + super().get_urls()

    @method_decorator(sensitive_post_parameters("support_token"))
    def support_match_view(self, request: HttpRequest, order_id: int) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        order = get_object_or_404(self.get_queryset(request), pk=order_id)
        if not self.has_view_permission(request, order):
            raise PermissionDenied
        try:
            matched = match_support_token(
                order_id=order.pk,
                actor=cast(User, request.user),
                support_token=request.POST.get("support_token", ""),
            )
        except ImproperlyConfigured, OrderSupportIdentifier.DoesNotExist:
            matched = None
        response = render(
            request,
            "admin/orders/support_match.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.opts,
                "order": order,
                "title": _("تطبیق شناسه‌ی پشتیبانی"),
                "matched": matched,
            },
            status=503 if matched is None else 200,
        )
        response["Referrer-Policy"] = "no-referrer"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    def input_action_view(
        self, request: HttpRequest, order_id: int, item_id: int, action: str
    ) -> HttpResponse:
        if not (
            self.has_view_permission(request)
            and request.user.has_perms(["orders.change_order", "orders.change_orderitem"])
        ):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        service = {"request": request_input, "receive": receive_input, "resume": resume_input}.get(
            action
        )
        if service is None:
            raise Http404
        order = get_object_or_404(self.get_queryset(request), pk=order_id)
        get_object_or_404(OrderItem.objects.only("pk"), pk=item_id, order_id=order_id)
        try:
            try:
                expected = int(request.POST.get("expected_event_id", ""))
            except ValueError:
                raise ValidationError(
                    _("وضعیت این محصول تغییر کرده است؛ صفحه را دوباره باز کنید.")
                ) from None
            service(
                order_id=order_id,
                item_id=item_id,
                actor=cast(User, request.user),
                expected_event_id=expected,
            )
        except ValidationError as error:
            return render(
                request,
                "admin/orders/input_action_error.html",
                {
                    **self.admin_site.each_context(request),
                    "opts": self.opts,
                    "order": order,
                    "title": _("اطلاعات فعال‌سازی"),
                    "error": " ".join(error.messages),
                },
                status=400,
            )
        return redirect("admin:orders_order_change", order_id)

    # A claim note may quote an account detail; keep it out of error reports.
    @method_decorator(sensitive_post_parameters("note"))
    def item_action_view(
        self, request: HttpRequest, order_id: int, item_id: int, action: str
    ) -> HttpResponse:
        """Opens or rejects a warranty claim, records a supply delay, or cancels a paid item
        with its refund, each with a note."""
        if not (
            self.has_view_permission(request)
            and request.user.has_perms(["orders.change_order", "orders.change_orderitem"])
        ):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        actions: dict[str, tuple[Callable[..., OrderItem], Any]] = {
            "claim": (open_claim, _("درخواست تعویض")),
            "reject": (reject_claim, _("رد درخواست تعویض")),
            "delay": (delay_supply, _("تأخیر تأمین")),
            "cancel": (cancel_paid_item, _("لغو و بازپرداخت")),
        }
        if action not in actions:
            raise Http404
        if action == "cancel" and not request.user.has_perm("payments.add_refund"):
            raise PermissionDenied
        service, title = actions[action]
        order = get_object_or_404(self.get_queryset(request), pk=order_id)
        get_object_or_404(OrderItem.objects.only("pk"), pk=item_id, order_id=order_id)
        changes: dict[str, Any] = {}
        try:
            try:
                expected = int(request.POST.get("expected_event_id", ""))
            except ValueError:
                raise ValidationError(
                    _("وضعیت این محصول تغییر کرده است؛ صفحه را دوباره باز کنید.")
                ) from None
            if action == "delay":
                try:
                    changes["hours"] = int(to_latin_digits(request.POST.get("hours", "")).strip())
                except ValueError:
                    raise ValidationError(_("ساعت تأخیر را عددی از ۱ تا ۷۲ بنویسید.")) from None
            if action == "cancel":
                changes["reason"] = request.POST.get("reason", "")
            service(
                order_id=order_id,
                item_id=item_id,
                actor=cast(User, request.user),
                expected_event_id=expected,
                note=request.POST.get("note", ""),
                **changes,
            )
        except ValidationError as error:
            return render(
                request,
                "admin/orders/input_action_error.html",
                {
                    **self.admin_site.each_context(request),
                    "opts": self.opts,
                    "order": order,
                    "title": title,
                    "error": " ".join(error.messages),
                },
                status=400,
            )
        if action in ("reject", "cancel"):
            return redirect("admin:orders_order_change", order_id)
        return redirect("admin:orders_order_deliver", order_id, item_id)

    def reveal_view(
        self, request: HttpRequest, order_id: int, item_id: int, target: str
    ) -> HttpResponse:
        # Refused before any lookup, so an unpermitted account cannot tell which orders exist.
        inline = OrderItemInline(Order, self.admin_site)
        if not (self.has_view_permission(request) and inline.has_view_permission(request)):
            raise PermissionDenied
        order = get_object_or_404(self.get_queryset(request), pk=order_id)
        item = get_object_or_404(
            OrderItem.objects.defer("customer_input"), pk=item_id, order_id=order_id
        )
        if request.method == "GET":
            return redirect("admin:orders_order_change", order_id)
        if request.method != "POST":
            return HttpResponseNotAllowed(["GET", "POST"])
        actor, ip = cast(User, request.user), client_ip(request)
        if target == "delivery":
            template, title = "admin/orders/delivery_reveal.html", _("اطلاعات تحویل")
            context: dict[str, Any] = {
                "content": reveal_delivery(order_id=order_id, item_id=item_id, actor=actor, ip=ip)
            }
        else:
            template, title = "admin/orders/input_reveal.html", _("اطلاعات مشتری")
            context = {
                "input_value": reveal_customer_input(
                    order_id=order_id, item_id=item_id, actor=actor, ip=ip
                )
            }
        return _private(
            render(
                request,
                template,
                {
                    **self.admin_site.each_context(request),
                    **context,
                    "title": title,
                    "order": order,
                    "item": item,
                    "opts": self.opts,
                },
            )
        )

    @method_decorator(sensitive_post_parameters())
    def deliver_view(self, request: HttpRequest, order_id: int, item_id: int) -> HttpResponse:
        """Form, preview, confirm: typed values travel only inside the posted form."""
        if not (
            self.has_view_permission(request)
            and request.user.has_perms(["orders.change_order", "orders.change_orderitem"])
        ):
            raise PermissionDenied
        if request.method not in ("GET", "POST"):
            return HttpResponseNotAllowed(["GET", "POST"])
        order = get_object_or_404(self.get_queryset(request), pk=order_id)
        item = get_object_or_404(
            OrderItem.objects.defer("customer_input").select_related("plan__product"),
            pk=item_id,
            order_id=order_id,
        )
        event_id = item.events.order_by("-pk").values_list("pk", flat=True).first()
        # deliver (A5), claim (A9) and replace (A10a) are separate services; the page
        # only chooses which one this item's status allows.
        mode = {
            OrderItem.Status.QUEUED: None if item.sla_paused_at else "deliver",
            OrderItem.Status.DELIVERED: "claim",
            OrderItem.Status.REPLACEMENT_REQUESTED: "replace",
        }.get(cast(OrderItem.Status, item.status))
        if mode == "replace":
            expires, cost = item.expires_at, 0
        else:
            days = item.product_snapshot.get("duration_days")
            expires = timezone.localdate() + timedelta(days=days) if days else None
            cost = int(item.cost_snapshot)
        form = DeliveryForm(
            request.POST if request.method == "POST" else None,
            initial={
                "expected_event_id": event_id,
                "expires_on": format_jalali(expires, with_month_name=False),
                "actual_cost": to_persian_digits(cost),
            },
            warranty=item.product_snapshot.get("warranty"),
            replacement=mode == "replace",
        )
        step, preview, error = request.POST.get("step", ""), None, ""
        can_deliver = mode in ("deliver", "replace")
        if request.method == "POST" and form.is_valid():
            content = form.cleaned_data["content"]
            if step == "preview" and not can_deliver:
                error = str(_("این محصول آماده‌ی تحویل نیست."))
            elif step == "preview":
                preview = content
            elif step == "confirm":
                shared = {
                    "order_id": order_id,
                    "item_id": item_id,
                    "actor": cast(User, request.user),
                    "expected_event_id": form.cleaned_data["expected_event_id"],
                    "lines": content.lines,
                    "note": content.note,
                    "expires_at": content.expires_at,
                }
                try:
                    if mode == "replace":
                        replace_delivery(**shared, extra_cost=form.cleaned_data["actual_cost"])
                    else:
                        deliver_item(**shared, actual_cost=form.cleaned_data["actual_cost"])
                except ValidationError as failure:
                    error = " ".join(failure.messages)
                else:
                    return redirect("admin:orders_order_change", order_id)
        claim_note = (
            item.events.filter(to_status=OrderItem.Status.REPLACEMENT_REQUESTED)
            .order_by("-pk")
            .values_list("note", flat=True)
            .first()
            if mode == "replace"
            else None
        )
        # The reasons this item's status allows today, each with what it would owe; the
        # service reads the same refund_amount, so the page cannot promise another figure.
        cancel_options: list[tuple[str, Any, Any]] = []
        cancel_refusal = ""
        if request.user.has_perm("payments.add_refund"):
            for reason, statuses in PAID_CANCELLATIONS.items():
                if item.status not in statuses:
                    continue
                try:
                    amount = refund_amount(item, reason)
                except ValidationError as refusal:
                    cancel_refusal = cancel_refusal or " ".join(refusal.messages)
                else:
                    cancel_options.append((reason, OrderItem.CancelReason(reason).label, amount))
        bank_ref = None
        if request.user.has_perm("payments.view_payment"):
            bank_ref = (
                order.payments.filter(status="verified").values_list("bank_ref", flat=True).first()
            )
        response = render(
            request,
            "admin/orders/delivery.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.opts,
                "title": {
                    "claim": _("درخواست تعویض"),
                    "replace": _("تعویض محصول"),
                }.get(mode or "", _("تحویل محصول")),
                "order": order,
                "item": item,
                "form": form,
                "preview": preview,
                "error": error,
                "event_id": event_id,
                "bank_ref": bank_ref,
                "can_deliver": can_deliver,
                "mode": mode,
                "claim_note": claim_note,
                "cancel_options": cancel_options,
                "cancel_refusal": cancel_refusal,
            },
            status=400 if error or form.errors else 200,
        )
        return _private(response, referrer="same-origin")

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> Any:
        if not self.has_view_permission(request):
            raise PermissionDenied
        at = timezone.now()
        base = super().get_queryset(request)
        base, _duplicates = self.get_search_results(request, base, request.GET.get("q", ""))
        flags = {
            name: Exists(OrderItem.objects.filter(order_id=OuterRef("pk"), status=state))
            for name, state in _QUEUE_STATES.items()
        }
        counts = base.aggregate(**{name: Count("pk", filter=flag) for name, flag in flags.items()})
        tabs = []
        for name, label in (
            ("unpaid", _("منتظر پرداخت")),
            ("queued", _("در صف انجام")),
            ("waiting", _("منتظر اطلاعات مشتری")),
            ("claims", _("درخواست تعویض")),
            ("all", _("همه‌ی سفارش‌ها")),
        ):
            params = request.GET.copy()
            for key in ("p", "o", "all"):
                params.pop(key, None)
            params["tab"] = name
            tabs.append(
                {
                    "name": name,
                    "label": label,
                    "url": "?" + params.urlencode(),
                    "count": counts.get(name),
                }
            )
        state = UnpaidExpirySweep.objects.filter(pk=1).first()
        pending = OrderItem.objects.filter(
            order_id=OuterRef("pk"), status=OrderItem.Status.PENDING_PAYMENT
        )
        backlog = (
            Order.objects.filter(unpaid_expires_at__lte=timezone.now())
            .filter(Exists(pending))
            .count()
        )
        context = {
            **(extra_context or {}),
            "queue_counts": counts,
            "queue_tabs": tabs,
            "queue_tab": queue_tab(request),
            "queue_as_of": at,
            "expiry_state": state,
            "expiry_backlog": backlog,
            "expiry_stale": state is None
            or timezone.now() - state.last_success_at > timedelta(minutes=3),
        }
        return super().changelist_view(request, extra_context=context)

    def get_changelist(self, request: HttpRequest, **kwargs: Any) -> type[ChangeList]:
        return OrderChangeList

    def get_search_results(
        self, request: HttpRequest, queryset: QuerySet[Order], search_term: str
    ) -> tuple[QuerySet[Order], bool]:
        return super().get_search_results(request, queryset, to_latin_digits(search_term))

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Order | None = None,
    ) -> Any:
        if obj:
            if request.user.has_perms(["orders.change_order", "orders.change_orderitem"]):
                latest = dict(
                    OrderItemEvent.objects.filter(order_item__order=obj)
                    .values("order_item_id")
                    .annotate(event_id=Max("pk"))
                    .values_list("order_item_id", "event_id")
                )
                controls = []
                for item in obj.items.all():
                    if item.status == OrderItem.Status.QUEUED:
                        action, label = "request", _("ثبت درخواست اطلاعات")
                    elif item.status == OrderItem.Status.AWAITING_INPUT:
                        action = "receive" if item.due_at is None else "resume"
                        label = _("اطلاعات رسید؛ ادامه‌ی انجام")
                    else:
                        continue
                    controls.append(
                        {
                            "item": item,
                            "event_id": latest.get(item.pk),
                            "action": action,
                            "label": label,
                        }
                    )
                context["input_controls"] = controls
            context["purchase_groups"] = grouped_items(obj.items.all())
            context["order_timeline"] = self.history(obj)
            context["input_access_log"] = (
                CredentialAccessLog.objects.filter(order_item__order_id=obj.pk)
                .select_related("user")
                .annotate(item_name=_ITEM_NAME, item_plan=_ITEM_PLAN)
            )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Order]:
        queryset = super().get_queryset(request)
        items = OrderItem.objects.defer("customer_input").select_related("plan__product", "refund")
        tab_filters = False
        if request.resolver_match and request.resolver_match.url_name == "orders_order_changelist":
            tab = queue_tab(request)
            if tab in _QUEUE_STATES:
                state = _QUEUE_STATES[tab]
                tab_filters = True
                items = items.filter(status=state)
                queryset = queryset.filter(
                    Exists(OrderItem.objects.filter(order_id=OuterRef("pk"), status=state))
                )
                if tab == "unpaid":
                    queryset = queryset.order_by("unpaid_expires_at", "pk")
                else:
                    field = _QUEUE_ORDER[tab]
                    items = items.order_by(F(field).asc(nulls_last=True), "pk")
                    queryset = queryset.annotate(
                        queue_first=Min(f"items__{field}", filter=Q(items__status=state))
                    ).order_by(F("queue_first").asc(nulls_last=True), "pk")
        prefetches = [Prefetch("items", queryset=items)]
        if tab_filters:
            # A tab shows one kind of item, but the next step weighs all of them.
            every = OrderItem.objects.defer("customer_input").select_related("refund")
            prefetches.append(Prefetch("items", queryset=every, to_attr="every_item"))
        return queryset.select_related("user").prefetch_related(*prefetches)

    @admin.display(description=_("محصول و مهلت"))
    def queue_work(self, obj: Order) -> str:
        at = timezone.now()
        rows = []
        for item in obj.items.all():
            kind, text, since = "neutral", "", None
            if item.status == OrderItem.Status.AWAITING_INPUT and item.sla_paused_at is None:
                text, since = str(_("در انتظار از")), item.input_requested_at
            else:
                deadline = (
                    obj.unpaid_expires_at
                    if item.status == OrderItem.Status.PENDING_PAYMENT
                    else item.due_at
                )
                if item.status not in {
                    OrderItem.Status.PENDING_PAYMENT,
                    OrderItem.Status.QUEUED,
                    OrderItem.Status.AWAITING_INPUT,
                }:
                    deadline = None
                if deadline is not None:
                    paused = (
                        item.sla_paused_at is not None
                        and item.status == OrderItem.Status.AWAITING_INPUT
                    )
                    reference = at
                    if paused and item.sla_paused_at is not None:
                        reference = item.sla_paused_at
                    seconds = (deadline - reference).total_seconds()
                    kind = "paused" if paused else ("overdue" if seconds <= 0 else "remaining")
                    label = _("گذشته از مهلت") if seconds <= 0 else _("مانده")
                    text = f"{format_countdown(abs(int(seconds)))} · {label}"
                    if paused:
                        text = str(_("متوقف")) + " · " + text
                elif item.status == OrderItem.Status.QUEUED:
                    text = str(_("مهلت ثبت نشده است"))
            refund = getattr(item, "refund", None)
            state = order_status.item_state(item, refund, obj.unpaid_expires_at, at)
            tone = order_status.STATES[state][1]
            rows.append({"item": item, "kind": kind, "text": text, "since": since, "tone": tone})
        every = getattr(obj, "every_item", None)
        return render_to_string(
            "admin/orders/queue_items.html",
            {
                "queue_items": rows,
                "next": _next_step(
                    obj.items.all() if every is None else every, obj.unpaid_expires_at, at
                ),
            },
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.display(description=_("شماره‌ی سفارش"), ordering="order_number")
    def number(self, obj: Order) -> str:
        return format_html(
            '<span class="order-number">{}</span><span class="order-list-mobile">'
            "<span>{}</span><strong>{}</strong><span>{}</span></span>",
            to_persian_digits(obj.order_number),
            self.customer(obj),
            self.amount(obj),
            self.item_states(obj),
        )

    @admin.display(description=_("مشتری"))
    def customer(self, obj: Order) -> str:
        return format_html('<bdi class="order-customer" dir="ltr">{}</bdi>', obj.user.email)

    @admin.display(description=_("جمع نهایی"), ordering="total_amount")
    def amount(self, obj: Order) -> str:
        return format_toman(obj.total_amount)

    @admin.display(description=_("جمع اقلام"))
    def subtotal_amount(self, obj: Order) -> str:
        return format_toman(obj.subtotal)

    @admin.display(description=_("تخفیف"))
    def discount(self, obj: Order) -> str:
        return format_toman(obj.discount_amount, with_unit=False) + " تومان"

    @admin.display(description=_("وضعیت آیتم‌ها"))
    def item_states(self, obj: Order) -> str:
        states = dict.fromkeys(
            (order_status.item_tone(item), item.status, item.get_status_display())
            for item in obj.items.all()
        )
        return format_html_join(
            " ", '<span class="order-status" data-tone="{}" data-state="{}">{}</span>', states
        )

    @admin.display(description=_("زمان ثبت"), ordering="created_at")
    def placed(self, obj: Order) -> str:
        return format_jalali(obj.created_at)

    @admin.display(description=_("سابقه‌ی سفارش"))
    def history(self, obj: Order) -> str:
        events = (
            OrderItemEvent.objects.filter(order_item__order=obj)
            .annotate(item_name=_ITEM_NAME, item_plan=_ITEM_PLAN)
            .order_by("created_at", "pk")
        )
        rows = format_html_join(
            "",
            '<li><div class="order-timeline__head">{}<span>{}</span></div>'
            '<p class="order-timeline__meta">{} · {} · {} · {}</p>'
            '<p class="order-timeline__note">{}</p></li>',
            (
                (
                    format_html(
                        '<span class="order-status" data-tone="{}" data-state="{}">{}</span>',
                        order_status.STATES[order_status.tracking_state(event.to_status)][1],
                        event.to_status,
                        event.get_to_status_display(),
                    ),
                    format_jalali(event.created_at),
                    format_html(
                        "<bdi>{}</bdi>؛ <bdi>{}</bdi>", event.item_name or "", event.item_plan or ""
                    ),
                    _("آیتم %(number)s") % {"number": to_persian_digits(event.order_item_id)},
                    event.get_actor_display(),
                    (
                        _("وضعیت قبلی: %(state)s") % {"state": event.get_from_status_display()}
                        if event.from_status
                        else _("ثبت سفارش")
                    ),
                    event.note,
                )
                for event in events
            ),
        )
        return format_html('<ol class="order-timeline">{}</ol>', rows)


class DiscountForm(forms.ModelForm):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["value"].label = _("میزان تخفیف")
        self.fields["scope"].label = _("روی کدام محصولات")
        self.fields["value"].help_text = _(
            "برای تخفیف درصدی، درصد؛ برای مبلغ ثابت، تومان وارد کنید."
        )
        for name in ("max_uses", "per_user_limit"):
            self.fields[name].help_text = _(
                "خالی یعنی بدون محدودیت. استفاده‌های لغوشده حساب نمی‌شوند."
            )

    def clean_code(self) -> str:
        return self.cleaned_data["code"].strip().upper()

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for name in ("value", "max_uses", "per_user_limit"):
            if cleaned.get(name) is not None and cleaned[name] <= 0:
                self.add_error(name, _("عددی بزرگ‌تر از صفر وارد کنید."))
        minimum = cleaned.get("min_order_amount")
        if minimum is not None and minimum < 0:
            self.add_error("min_order_amount", _("صفر یا عددی بزرگ‌تر وارد کنید."))
        start, end = cleaned.get("valid_from"), cleaned.get("valid_until")
        if start and end and start >= end:
            self.add_error("valid_until", _("پایان اعتبار باید پس از شروع آن باشد."))
        if cleaned.get("scope") == DiscountCode.Scope.SELECTED and not cleaned.get("products"):
            self.add_error("products", _("حداقل یک محصول برای این کد تخفیف انتخاب کنید."))
        return cleaned


class RedemptionInline(TabularInline):
    model = DiscountRedemption
    fields = ("order", "user", "discount_amount", "used", "released")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.display(description=_("مبلغ تخفیف"))
    def discount_amount(self, obj: DiscountRedemption) -> str:
        return format_toman(obj.amount)

    @admin.display(description=_("زمان استفاده"))
    def used(self, obj: DiscountRedemption) -> str:
        return format_jalali(obj.created_at)

    @admin.display(description=_("زمان آزادسازی"))
    def released(self, obj: DiscountRedemption) -> str:
        return format_jalali(obj.released_at) if obj.released_at else "—"


@admin.register(DiscountCode)
class DiscountAdmin(ModelAdmin):
    form = DiscountForm
    list_display = ("code", "discount_value", "is_active", "active_uses", "limit")
    search_fields = ("code",)
    list_filter = ("is_active", "kind", "scope")
    filter_horizontal = ("products",)
    exclude = ("used_count", "created_at")
    readonly_fields = ("active_uses", "created")
    inlines = (RedemptionInline,)

    @admin.display(description=_("تخفیف"))
    def discount_value(self, obj: DiscountCode) -> str:
        if obj.kind == DiscountCode.Kind.PERCENT:
            return to_persian_digits(obj.value) + " " + _("درصد")
        return format_toman(obj.value)

    @admin.display(description=_("تعداد استفاده‌ی فعال"))
    def active_uses(self, obj: DiscountCode) -> str:
        return to_persian_digits(obj.used_count)

    @admin.display(description=_("حداکثر تعداد استفاده"))
    def limit(self, obj: DiscountCode) -> str:
        return (
            to_persian_digits(obj.max_uses) if obj.max_uses is not None else str(_("بدون محدودیت"))
        )

    @admin.display(description=_("زمان ایجاد"))
    def created(self, obj: DiscountCode) -> str:
        return format_jalali(obj.created_at)

    def save_model(self, request: HttpRequest, obj: DiscountCode, form: Any, change: bool) -> None:
        # M2M inserts check product foreign keys after saving the code.
        list(
            Product.objects.select_for_update()
            .filter(pk__in=form.cleaned_data["products"])
            .order_by("pk")
        )
        if change:
            obj.used_count = DiscountCode.objects.select_for_update().get(pk=obj.pk).used_count
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

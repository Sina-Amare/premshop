"""Read-only purchase records in the existing operator panel."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django import forms
from django.contrib import admin
from django.db.models import Exists, OuterRef, Prefetch, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline

from apps.catalog.models import Product
from apps.core.formatting import format_jalali, format_toman, to_persian_digits
from apps.orders.models import (
    DiscountCode,
    DiscountRedemption,
    Order,
    OrderItem,
    OrderItemEvent,
    UnpaidExpirySweep,
)


class OrderItemInline(TabularInline):
    model = OrderItem
    fields = ("purchase", "status", "sale_amount", "cost_amount", "cancel_reason")
    readonly_fields = fields
    extra = 0
    can_delete = False
    classes = ("order-items",)
    show_title = False

    def get_queryset(self, request: HttpRequest) -> QuerySet[OrderItem]:
        return super().get_queryset(request).defer("customer_input")

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

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


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    change_list_template = "admin/orders/change_list.html"
    list_display = ("number", "customer", "amount", "item_states", "placed")
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
    )
    fieldsets = (
        (_("مشخصات سفارش"), {"fields": (("number", "customer"), ("channel", "placed"))}),
        (_("مبلغ‌های سفارش"), {"fields": (("subtotal_amount", "discount", "amount"),)}),
    )
    change_form_after_template = "admin/orders/history.html"

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> Any:
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
            "expiry_state": state,
            "expiry_backlog": backlog,
            "expiry_stale": state is None
            or timezone.now() - state.last_success_at > timedelta(minutes=3),
        }
        return super().changelist_view(request, extra_context=context)

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
            context["order_timeline"] = self.history(obj)
        return super().render_change_form(request, context, add, change, form_url, obj)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Order]:
        return (
            super()
            .get_queryset(request)
            .select_related("user")
            .prefetch_related(Prefetch("items", queryset=OrderItem.objects.defer("customer_input")))
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
        states = dict.fromkeys((item.status, item.get_status_display()) for item in obj.items.all())
        return format_html_join(" ", '<span class="order-status" data-state="{}">{}</span>', states)

    @admin.display(description=_("زمان ثبت"), ordering="created_at")
    def placed(self, obj: Order) -> str:
        return format_jalali(obj.created_at)

    @admin.display(description=_("سابقه‌ی سفارش"))
    def history(self, obj: Order) -> str:
        events = OrderItemEvent.objects.filter(order_item__order=obj).order_by("created_at", "pk")
        rows = format_html_join(
            "",
            '<li><div class="order-timeline__head"><strong>{}</strong><span>{}</span></div>'
            '<p class="order-timeline__meta">{} · {} · {}</p>'
            '<p class="order-timeline__note">{}</p></li>',
            (
                (
                    event.get_to_status_display(),
                    format_jalali(event.created_at),
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

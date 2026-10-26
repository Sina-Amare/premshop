"""Read-only purchase records in the existing operator panel."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import Prefetch, QuerySet
from django.http import HttpRequest
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline

from apps.core.formatting import format_jalali, format_toman, to_persian_digits
from apps.orders.models import Order, OrderItem, OrderItemEvent


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

    @admin.display(description=_("شمارهٔ سفارش"), ordering="order_number")
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

    @admin.display(description=_("سابقهٔ سفارش"))
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

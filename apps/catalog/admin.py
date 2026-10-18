"""The operator's tool until S7: a product, its specs and its plans on one form.

The bar from the step plan: a full product — specs and two plans — in under two
minutes. Everything here serves that: inlines instead of three separate pages,
spec titles suggested from the ones already used, and the promotion rules
surfacing as Persian form errors (they are database CHECKs that Django validates
in full_clean, so no custom clean() is needed and the form and the database can
never disagree).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import display

from apps.catalog.models import Category, Plan, Product, ProductSpec
from apps.core.formatting import (
    format_countdown,
    format_jalali,
    format_toman,
    to_persian_digits,
)


@dataclass(frozen=True)
class Thumbnail:
    """What Unfold's header display wants in the image slot.

    It reads `.path` and puts it straight into an <img src>, so despite the
    name it must be a URL. Handing it Django's ImageFieldFile instead renders
    `file:///C:/...`, which every browser refuses to load — the whole column
    of marks was broken that way before this existed, with nothing failing
    server-side to say so.
    """

    path: str
    squared: bool = True
    borderless: bool = False


class SuggestingTextInput(forms.TextInput):
    """A text input with a <datalist> of titles already in use.

    The operator types «ری» and sees «ریجن»; the same fact is spelled the same
    way on every product, which is what makes a spec table read as one system
    rather than as thirty separate opinions. Free text is still allowed — the
    list suggests, it does not constrain.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.attrs.setdefault("list", "spec-title-suggestions")

    def render(self, name: str, value: Any, attrs: Any = None, renderer: Any = None) -> SafeString:
        titles = (
            ProductSpec.objects.order_by("title").values_list("title", flat=True).distinct()[:200]
        )
        options = format_html_join_safe(titles)
        return super().render(name, value, attrs, renderer) + format_html(
            '<datalist id="spec-title-suggestions">{}</datalist>', options
        )


def format_html_join_safe(values: Any) -> SafeString:
    from django.utils.html import format_html_join

    return format_html_join("", '<option value="{}"></option>', ((v,) for v in values))


class ProductSpecForm(forms.ModelForm):
    class Meta:
        model = ProductSpec
        fields = ["title", "value", "is_key", "sort_order"]
        widgets = {"title": SuggestingTextInput}


class ProductSpecInline(TabularInline):
    model = ProductSpec
    form = ProductSpecForm
    extra = 3
    show_title = False
    fields = ["title", "value", "is_key"]
    ordering = ["sort_order", "id"]
    # Drag the row instead of typing a number into it. The owner could not
    # read «ترتیب نمایش» and no wording fixes that: a sort key is a control
    # problem, not a labelling one. Unfold hides the column and writes the
    # field itself.
    ordering_field = "sort_order"
    hide_ordering_field = True


class PlanInline(StackedInline):
    model = Plan
    extra = 1
    show_title = False
    ordering = ["sort_order", "id"]
    ordering_field = "sort_order"
    hide_ordering_field = True
    fieldsets = [
        (
            None,
            {
                "fields": [
                    ("title", "duration_days", "sort_order"),
                    ("cost_price", "sale_price"),
                    ("is_available", "max_quantity"),
                ]
            },
        ),
        ("تخفیف", {"fields": ["promo_price", ("promo_starts_at", "promo_ends_at")]}),
        (
            "اطلاعات مشتری و تأمین",
            {"fields": [("requires_customer_input", "customer_input_label"), "supplier_url"]},
        ),
    ]


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ["name", "slug", "sort_order", "product_count"]
    list_editable = ["sort_order"]
    search_fields = ["name"]
    fields = [
        "name",
        "slug",
        "description",
        "intro_html",
        "sort_order",
        "seo_title",
        "seo_description",
    ]

    @admin.display(description="تعداد محصول")
    def product_count(self, obj: Category) -> int:
        return obj.products.count()


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ["product", "status_label", "price_range", "promo_left", "updated"]
    # Status earns a filter. Warranty and delivery type are facts you read off
    # the row, not axes you slice on, and at a dozen products the filter column
    # was taller than the result.
    list_filter = ["status", "category"]
    actions = ["publish", "unpublish"]
    search_fields = ["name", "short_description", "aliases"]
    list_select_related = ["category"]
    inlines = [ProductSpecInline, PlanInline]
    # The table is the operator's working surface; the centred container wasted
    # a third of a wide screen on margins.
    list_fullwidth = True
    # The product form is long enough to lose work in. The browser asks before
    # a half-finished product is abandoned.
    warn_unsaved_form = True
    fieldsets = [
        (None, {"fields": ["name", "slug", "category", "status", "image"]}),
        (
            "تحویل",
            {
                "fields": [
                    "delivery_type",
                    ("warranty", "renewable"),
                    ("delivery_hours", "delivery_note"),
                    "delivery_template",
                ]
            },
        ),
        ("متن", {"fields": ["short_description", "full_description"]}),
        ("جستجو", {"fields": ["aliases"], "classes": ["collapse"]}),
        ("سئو", {"fields": ["seo_title", "seo_description"], "classes": ["collapse"]}),
    ]

    @display(description="محصول", header=True, ordering="name")
    def product(self, obj: Product) -> list[Any]:
        """Mark, name and department in one column.

        The mark is how a Persian shopper — and the operator — finds a product
        first; the list used to be ten rows of text that all began the same
        way. Unfold's header display takes (title, subtitle, initials, image)
        and falls back to the initials when a product has no picture yet.
        """
        mark = Thumbnail(obj.image.url) if obj.image else None
        return [obj.name, obj.category.name, obj.name[:1], mark]

    @display(
        description="وضعیت",
        ordering="status",
        # Keyed off the enum, not off the Persian words. Unfold looks the
        # value up with `value in label` and falls through to neutral grey on
        # a miss, so renaming a label in models.py would have turned every
        # badge grey with nothing raising — the same silent fall-through that
        # made «Vazirmatn» vs «Vazir» change nothing on screen.
        #
        # Draft is `info`, not `warning`: Unfold's warning variant renders
        # orange, and this project reserves amber for time and nothing else
        # (ADR-0016). A draft is not a clock.
        label={
            Product.Status.ACTIVE.label: "success",
            Product.Status.DRAFT.label: "info",
            Product.Status.UNAVAILABLE.label: "danger",
        },
    )
    def status_label(self, obj: Product) -> str:
        """Draft and unavailable are the two states that cost money when they
        go unnoticed — one hides a product that should be selling, the other
        advertises one that cannot be delivered. A coloured badge is read at a
        glance where a word in a column is not."""
        return obj.get_status_display()

    @display(description="تخفیف", label={"تمام شد": "danger"})
    def promo_left(self, obj: Product) -> str:
        """How long the discount has. The catalogue's only real clock, and it
        was invisible from the list: to learn a promotion ended tonight the
        operator had to open the product and read two datetime fields.

        Amber is spent here and nowhere else in this panel (ADR-0016). The
        plan is the cheapest one, which is the price the row already shows.
        """
        plan = obj.cheapest_plan
        if plan is None or not plan.promotion_is_active:
            return "—"
        left = plan.promo_seconds_left
        if left is None:
            return "بدون مهلت"
        return format_countdown(left) if left else "تمام شد"

    # Django's own bulk actions rather than Unfold's row buttons. Unfold
    # renders a row action as a plain link, so the handler changes state on a
    # GET: an <img src="…/unpublish/"> on any page the operator visits while
    # signed in would take a product off the shop. Django's actions POST with
    # a CSRF token and enforce `permissions` for free, and publishing several
    # products at once is the commoner job anyway.
    @admin.action(description="انتشار", permissions=["change"])
    def publish(self, request: HttpRequest, queryset: Any) -> None:
        self._set_status(request, queryset, Product.Status.ACTIVE)

    @admin.action(description="برداشتن از سایت", permissions=["change"])
    def unpublish(self, request: HttpRequest, queryset: Any) -> None:
        self._set_status(request, queryset, Product.Status.DRAFT)

    def _set_status(self, request: HttpRequest, queryset: Any, status: str) -> None:
        changed = queryset.update(status=status, updated_at=timezone.now())
        label = Product.Status(status).label
        messages.success(request, f"{to_persian_digits(changed)} محصول {label} شد.")

    @admin.display(description="قیمت")
    def price_range(self, obj: Product) -> str:
        prices = [plan.effective_price for plan in obj.plans.all() if plan.is_available]
        if not prices:
            return "—"
        low, high = min(prices), max(prices)
        if low == high:
            return format_toman(low)
        return f"{format_toman(low, with_unit=False)} تا {format_toman(high)}"

    @admin.display(description="به‌روزرسانی", ordering="updated_at")
    def updated(self, obj: Product) -> str:
        """Jalali, like every other date this shop shows. The admin was
        printing «۱۸ اکتبر ۲۰۲۶» at an operator who works in Mehr and Aban —
        a Gregorian month name is a date they have to convert in their head."""
        return format_jalali(obj.updated_at)

    def get_queryset(self, request: Any) -> Any:
        return super().get_queryset(request).prefetch_related("plans")


@admin.register(Plan)
class PlanAdmin(ModelAdmin):
    """A read-across of every plan, which the product form cannot give.

    Editing stays on the product, where a plan belongs next to the thing it
    prices — this page exists for the two questions a product form can never
    answer: which discounts are running right now, and where the margin is
    thin. Both needed opening twelve forms before.
    """

    list_display = ["plan", "price", "margin", "promo_window", "is_available"]
    list_filter = ["is_available", "product__category"]
    search_fields = ["product__name", "title"]
    list_select_related = ["product"]
    list_fullwidth = True
    ordering = ["product__name", "sort_order"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        # A plan with no product is meaningless; it is added on the product.
        return False

    @display(description="پلن", header=True, ordering="product__name")
    def plan(self, obj: Plan) -> list[Any]:
        mark = Thumbnail(obj.product.image.url) if obj.product.image else None
        return [obj.product.name, obj.title, obj.product.name[:1], mark]

    @admin.display(description="قیمت", ordering="sale_price")
    def price(self, obj: Plan) -> str:
        effective = obj.effective_price
        if effective == obj.sale_price:
            return format_toman(effective)
        # The struck original matters here: this page is where a wrong
        # promotion is spotted.
        return format_html(
            '<span style="text-decoration:line-through;opacity:.55">{}</span> {}',
            format_toman(obj.sale_price, with_unit=False),
            format_toman(effective),
        )

    @display(description="سود", label=True)
    def margin(self, obj: Plan) -> str:
        """What the plan actually earns at the price it is selling for today.

        `cost_price`'s own help text says it is «برای حساب سود» and nothing in
        the panel ever did that sum. Routed through `effective_price` so a
        running discount is counted, rather than a second price rule.
        """
        profit = obj.effective_price - obj.cost_price
        if obj.effective_price <= 0:
            return "—"
        share = round(profit / obj.effective_price * 100)
        return f"{format_toman(profit)} ({to_persian_digits(share)}٪)"

    @display(description="تخفیف")
    def promo_window(self, obj: Plan) -> str:
        if not obj.promotion_is_active:
            return "—"
        left = obj.promo_seconds_left
        return format_countdown(left) if left is not None else "بدون مهلت"

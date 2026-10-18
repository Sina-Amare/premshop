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
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import SafeString
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import display

from apps.catalog.models import Category, Plan, Product, ProductSpec
from apps.core.formatting import format_jalali, format_toman


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
    fields = ["title", "value", "is_key", "sort_order"]
    ordering = ["sort_order", "id"]


class PlanInline(StackedInline):
    model = Plan
    extra = 1
    show_title = False
    ordering = ["sort_order", "id"]
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
    list_display = ["product", "status_label", "warranty", "price_range", "updated"]
    list_filter = ["status", "category", "warranty", "delivery_type"]
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

"""The operator's tool until S7: a product, its specs and its plans on one form.

The bar from the step plan: a full product — specs and two plans — in under two
minutes. Everything here serves that: inlines instead of three separate pages,
spec titles suggested from the ones already used, and the promotion rules
surfacing as Persian form errors (they are database CHECKs that Django validates
in full_clean, so no custom clean() is needed and the form and the database can
never disagree).
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import SafeString
from unfold.admin import ModelAdmin, StackedInline, TabularInline

from apps.catalog.models import Category, Plan, Product, ProductSpec
from apps.core.formatting import format_toman


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
        fields = ["title", "value", "sort_order"]
        widgets = {"title": SuggestingTextInput}


class ProductSpecInline(TabularInline):
    model = ProductSpec
    form = ProductSpecForm
    extra = 3
    hide_title = True
    fields = ["title", "value", "sort_order"]
    ordering = ["sort_order", "id"]


class PlanInline(StackedInline):
    model = Plan
    extra = 1
    hide_title = True
    ordering = ["sort_order", "id"]
    fieldsets = [
        (
            None,
            {
                "fields": [
                    ("title", "duration_days", "sort_order"),
                    ("cost_price", "sale_price"),
                    "is_available",
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
    list_display = ["name", "category", "status", "region", "warranty", "price_range", "updated"]
    list_filter = ["status", "category", "region", "warranty", "delivery_type"]
    search_fields = ["name", "short_description"]
    list_select_related = ["category"]
    inlines = [ProductSpecInline, PlanInline]
    fieldsets = [
        (None, {"fields": ["name", "slug", "category", "status", "image"]}),
        (
            "تحویل",
            {
                "fields": [
                    "delivery_type",
                    "region",
                    "warranty",
                    "delivery_hours",
                    "delivery_template",
                ]
            },
        ),
        ("متن", {"fields": ["short_description", "full_description"]}),
        ("سئو", {"fields": ["seo_title", "seo_description"], "classes": ["collapse"]}),
    ]

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
    def updated(self, obj: Product) -> Any:
        return obj.updated_at

    def get_queryset(self, request: Any) -> Any:
        return super().get_queryset(request).prefetch_related("plans")

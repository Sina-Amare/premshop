"""The operator's tool until S7: a product, its specs and its plans on one form.

The bar from the step plan: a full product — specs and two plans — in under two
minutes. Everything here serves that: inlines instead of three separate pages,
spec titles suggested from the ones already used, and the promotion rules
surfacing as Persian form errors under the field they are about (they are
database CHECKs that Django validates in full_clean, so the form and the
database can never disagree; models.ChecksNameTheirField files each message).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import display
from unfold.widgets import UnfoldAdminTextInputWidget

from apps.catalog.models import Category, Plan, Product, ProductSpec
from apps.core.formatting import (
    format_countdown,
    format_jalali,
    format_toman,
    to_persian_digits,
)

PRICE_FIELDS = ("cost_price", "sale_price", "promo_price")

# Unfold's badge variant per status, keyed off the stored value. A label is a
# translated word, so a map keyed off labels greys every badge the moment the
# language switch is used, and Unfold falls through to grey without raising.
#
# Draft is `info`, not `warning`: Unfold's warning variant renders orange, and
# this project reserves amber for time and nothing else (ADR-0016).
STATUS_VARIANTS: dict[str, str] = {
    Product.Status.ACTIVE: "success",
    Product.Status.DRAFT: "info",
    Product.Status.UNAVAILABLE: "danger",
}


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


class SuggestingTextInput(UnfoldAdminTextInputWidget):
    """A text input with a <datalist> of titles already in use.

    The operator types «ری» and sees «ریجن»; the same fact is spelled the same
    way on every product, which is what makes a spec table read as one system
    rather than as thirty separate opinions. Free text is still allowed — the
    list suggests, it does not constrain.

    Unfold's own text widget, so the box has Unfold's border: a plain
    TextInput rendered borderless and invisible in an empty row.
    """

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        super().__init__({**(attrs or {}), "list": "spec-title-suggestions"})

    def render(self, name: str, value: Any, attrs: Any = None, renderer: Any = None) -> SafeString:
        titles = (
            ProductSpec.objects.order_by("title").values_list("title", flat=True).distinct()[:200]
        )
        options = format_html_join("", '<option value="{}"></option>', ((t,) for t in titles))
        return super().render(name, value, attrs, renderer) + format_html(
            '<datalist id="spec-title-suggestions">{}</datalist>', options
        )


class ProductSpecForm(forms.ModelForm):
    class Meta:
        model = ProductSpec
        fields = ["title", "value", "is_key", "sort_order"]
        widgets = {"title": SuggestingTextInput}

    def has_changed(self) -> bool:
        # Unfold numbers every row of the table on a drop, the blank extra
        # rows too. Counted as a change, the move made the formset demand a
        # title for each blank row, and every drag ended in a failed save.
        if self.instance.pk is None:
            return any(name != "sort_order" for name in self.changed_data)
        return super().has_changed()


class ProductSpecInline(TabularInline):
    model = ProductSpec
    form = ProductSpecForm
    extra = 3
    show_title = False
    # `sort_order` has to be among the fields even though nobody types it:
    # Unfold's drag writes the new order into that input, and without the
    # input a drag reordered the rows on screen and saved nothing.
    fields = ["title", "value", "is_key", "sort_order"]
    ordering = ["sort_order", "id"]
    # Drag the row instead of typing a number into it. The owner could not
    # read «ترتیب نمایش» and no wording fixes that: a sort key is a control
    # problem, not a labelling one.
    ordering_field = "sort_order"
    hide_ordering_field = True


class PlanForm(forms.ModelForm):
    """A discount's start and its end are a date box and a time box each, and
    the four were named only by their placeholders, «تاریخ» and «ساعت»: a
    screen reader heard the same two words twice per plan. Django points no
    split field at its help, so that is done here too.

    No Meta: the inline gives the model and its fieldsets the fields."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in ("promo_starts_at", "promo_ends_at"):
            if name not in self.fields:
                continue
            label = self.fields[name].label
            date, time = self.fields[name].widget.widgets
            date.attrs["aria-label"] = gettext("تاریخ %(label)s") % {"label": label}
            time.attrs["aria-label"] = gettext("ساعت %(label)s") % {"label": label}
            for box in (date, time):
                box.attrs["aria-describedby"] = f"{self[name].auto_id}_helptext"


class PlanInline(StackedInline):
    model = Plan
    form = PlanForm
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
                    ("title", "duration_days"),
                    ("cost_price", "sale_price"),
                    ("is_available", "max_quantity"),
                    # A row of its own: Unfold hides the whole row that holds
                    # the ordering field, and while it shared one with title
                    # and duration no screen could add a plan.
                    "sort_order",
                ]
            },
        ),
        (_("تخفیف"), {"fields": ["promo_price", ("promo_starts_at", "promo_ends_at")]}),
        (
            _("اطلاعات مشتری و تأمین"),
            {"fields": [("requires_customer_input", "customer_input_label"), "supplier_url"]},
        ),
    ]


def amount(value: Decimal) -> SafeString:
    """A toman figure that never breaks across lines; «تومان» used to land a
    line below its number in a narrow column."""
    return format_html('<span class="panel-amount">{}</span>', format_toman(value))


def countdown(seconds: int) -> SafeString:
    """Time left on a discount, in the time colour (ADR-0016)."""
    return format_html('<span class="panel-time">{}</span>', format_countdown(seconds))


def _money(value: Decimal | None) -> str:
    return "خالی" if value is None else format_toman(value)


def price_changes(form: forms.ModelForm) -> list[str]:
    """Each price `form` changed, as «قیمت فروش از ۲۰۰,۰۰۰ تومان به ۲۵۰,۰۰۰ تومان».

    Built with translations off, like Django's own change messages, so the
    history reads the same whichever language the operator had chosen.
    """
    with translation.override(None):
        return [
            f"{form.fields[name].label} از {_money(form.initial.get(name))} "
            f"به {_money(form.cleaned_data.get(name))}"
            for name in PRICE_FIELDS
            if name in form.changed_data
        ]


def with_price_changes(
    message: list[dict[str, Any]], form: forms.ModelForm, *, inline: bool
) -> None:
    """Swap the bare price labels in Django's change message for old and new.

    Django's message names an inline's object and leaves the main form's
    entry unnamed, which is how the entry for `form` is found.
    """
    changes = price_changes(form)
    if not changes:
        return
    with translation.override(None):
        labels = {str(form.fields[name].label) for name in PRICE_FIELDS}
        target = str(form.instance) if inline else None
    for entry in message:
        changed = entry.get("changed", {})
        if "fields" in changed and changed.get("object") == target:
            changed["fields"] = [f for f in changed["fields"] if f not in labels] + changes


def edited_plans(formset: Any) -> list[forms.ModelForm]:
    """The forms of plans that existed before this save and were changed."""
    return [
        plan_form
        for plan_form in formset.initial_forms
        if plan_form.has_changed() and plan_form not in formset.deleted_forms
    ]


def warn_if_below_cost(request: HttpRequest, plan: Plan) -> None:
    loss = plan.cost_price - plan.effective_price
    if plan.is_available and loss > 0:
        messages.warning(
            request,
            gettext("پلن «%(plan)s» زیر قیمت خرید فروخته می‌شود: %(loss)s زیان در هر فروش.")
            % {"plan": plan, "loss": format_toman(loss)},
        )


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ["name", "slug", "product_count"]
    # Dragged like specs and plans, where the number box was a full-width
    # input with no label of its own. Unfold adds the hidden field.
    ordering_field = "sort_order"
    hide_ordering_field = True
    # The drag is the order. With the model's name tiebreak the header
    # showed a Latin «2», the sort priority of the name column.
    ordering = ["sort_order"]
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

    @admin.display(description=_("تعداد محصول"))
    def product_count(self, obj: Category) -> str:
        return to_persian_digits(obj.products.count())


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ["product", "status_label", "price_range", "promo_left", "updated"]
    # Status and department are the two ways this list gets sliced. Warranty
    # and delivery type were filters too, and at a dozen products the filter
    # column stood taller than the result.
    list_filter = ["status", "category"]
    actions = ["publish", "unpublish"]
    # Only switches the search box on: get_search_results asks the shop's own
    # search, which ignores these fields.
    search_fields = ["name"]
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
            _("تحویل"),
            {
                "fields": [
                    "delivery_type",
                    ("warranty", "renewable"),
                    ("delivery_hours", "delivery_note"),
                    "delivery_template",
                ]
            },
        ),
        (_("متن"), {"fields": ["short_description", "full_description"]}),
        (_("جستجو"), {"fields": ["aliases"], "classes": ["collapse"]}),
        (_("سئو"), {"fields": ["seo_title", "seo_description"], "classes": ["collapse"]}),
    ]

    @display(description=_("محصول"), header=True, ordering="name")
    def product(self, obj: Product) -> list[Any]:
        """Mark, name and department in one column.

        The mark is how a Persian shopper — and the operator — finds a product
        first; the list used to be ten rows of text that all began the same
        way. Unfold's header display takes (title, subtitle, initials, image)
        and falls back to the initials when a product has no picture yet.

        On a phone the status and price columns are past the edge of the
        screen, so the same two facts ride under the name, shown only there.
        """
        mark = Thumbnail(obj.image.url) if obj.image else None
        badge = render_to_string(
            "unfold/helpers/label.html",
            {"text": obj.get_status_display(), "type": STATUS_VARIANTS.get(obj.status)},
        )
        under = format_html(
            '{}<span class="panel-narrow">{}{}</span>',
            obj.category.name,
            badge,
            self.price_range(obj),
        )
        return [obj.name, under, obj.name[:1], mark]

    @display(description=_("وضعیت"), ordering="status", label=STATUS_VARIANTS)
    def status_label(self, obj: Product) -> tuple[str, str]:
        """Draft and unavailable are the two states that cost money when they
        go unnoticed — one hides a product that should be selling, the other
        advertises one that cannot be delivered. A coloured badge is read at a
        glance where a word in a column is not."""
        return obj.status, obj.get_status_display()

    @admin.display(description=_("تخفیف"))
    def promo_left(self, obj: Product) -> str:
        """How long the soonest-ending discount has, across every plan on sale.

        The catalogue's only real clock, and it was invisible from the list:
        to learn a promotion ended tonight the operator had to open the
        product and read two datetime fields. Reading only the cheapest plan
        hid three of the four discounts that were running.
        """
        running = [plan for plan in obj.available_plans() if plan.promotion_is_active]
        if not running:
            return "—"
        ends = [left for plan in running if (left := plan.promo_seconds_left) is not None]
        return countdown(min(ends)) if ends else gettext("بدون پایان")

    # Django's own bulk actions rather than Unfold's row buttons. Unfold
    # renders a row action as a plain link, so the handler changes state on a
    # GET: an <img src="…/unpublish/"> on any page the operator visits while
    # signed in would take a product off the shop. Django's actions POST with
    # a CSRF token and enforce `permissions` for free, and publishing several
    # products at once is the commoner job anyway.
    @admin.action(description=_("انتشار"), permissions=["change"])
    def publish(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        self._set_status(request, queryset, Product.Status.ACTIVE)

    @admin.action(description=_("برداشتن از سایت"), permissions=["change"])
    def unpublish(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        self._set_status(request, queryset, Product.Status.DRAFT)

    def _set_status(
        self, request: HttpRequest, queryset: QuerySet[Product], status: Product.Status
    ) -> None:
        """Change the status and write it into each product's history, so the
        history page says who published or withdrew a product, and when."""
        changing = list(queryset.exclude(status=status))
        Product.objects.filter(pk__in=[p.pk for p in changing]).update(
            status=status, updated_at=timezone.now()
        )
        with translation.override(None):
            label = Product._meta.get_field("status").verbose_name
            for product in changing:
                old = Product.Status(product.status).label
                change = f"{label} از «{old}» به «{status.label}»"
                self.log_change(request, product, [{"changed": {"fields": [change]}}])
        messages.success(
            request,
            gettext("%(count)s محصول %(status)s شد.")
            % {"count": to_persian_digits(len(changing)), "status": status.label},
        )

    @admin.display(description=_("قیمت"))
    def price_range(self, obj: Product) -> str:
        prices = [plan.effective_price for plan in obj.available_plans()]
        if not prices:
            return "—"
        low, high = min(prices), max(prices)
        if low == high:
            return amount(low)
        # One element of two unbreakable parts, «تا» with the second. Under the
        # name on a phone each piece was a flex item of its own, and «تا»
        # could stand alone on a line.
        return format_html(
            '<span class="panel-range"><span class="panel-amount">{}</span> '
            '<span class="panel-amount">{} {}</span></span>',
            format_toman(low, with_unit=False),
            gettext("تا"),
            format_toman(high),
        )

    @admin.display(description=_("به‌روزرسانی"), ordering="updated_at")
    def updated(self, obj: Product) -> str:
        """Jalali, like every other date this shop shows. The admin was
        printing «۱۸ اکتبر ۲۰۲۶» at an operator who works in Mehr and Aban —
        a Gregorian month name is a date they have to convert in their head."""
        return format_html('<span class="panel-date">{}</span>', format_jalali(obj.updated_at))

    def get_queryset(self, request: Any) -> Any:
        return super().get_queryset(request).prefetch_related("plans")

    def get_search_results(
        self, request: HttpRequest, queryset: QuerySet[Product], search_term: str
    ) -> tuple[QuerySet[Product], bool]:
        """The shop's search, so a spelling the storefront understands finds
        the product here too: Arabic-keyboard letters, half-spaces, the
        operator's own aliases and near misses."""
        if not search_term.strip():
            return queryset, False
        # The storefront's cap, before the trigram fallback runs on it.
        matches = Product.objects.search(search_term.strip()[:100]).values("pk")
        return queryset.filter(pk__in=matches), False

    def formfield_for_choice_field(self, db_field: Any, request: HttpRequest, **kwargs: Any) -> Any:
        if db_field.name == "delivery_type":
            # Unfold offers a blank only on blank=True fields, so a new product
            # arrived with «اکانت آماده» selected, a promise nobody made.
            # The blank is still refused on save: the field is required.
            kwargs["choices"] = db_field.get_choices(
                include_blank=True, blank_choice=[("", _("Select value"))]
            )
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def construct_change_message(
        self, request: HttpRequest, form: Any, formsets: Any, add: bool = False
    ) -> list[dict[str, Any]]:
        message = super().construct_change_message(request, form, formsets, add)
        for formset in formsets or []:
            if formset.model is Plan:
                for plan_form in edited_plans(formset):
                    with_price_changes(message, plan_form, inline=True)
        return message

    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        super().save_formset(request, form, formset, change)
        if formset.model is not Plan:
            return
        for plan_form in edited_plans(formset):
            if changes := price_changes(plan_form):
                # The plan's own history, beside the product's.
                self.log_change(request, plan_form.instance, [{"changed": {"fields": changes}}])
        saved = [*formset.new_objects, *(plan for plan, _fields in formset.changed_objects)]
        for plan in saved:
            warn_if_below_cost(request, plan)


@admin.register(Plan)
class PlanAdmin(ModelAdmin):
    """A read-across of every plan, which the product form cannot give.

    Editing stays on the product, where a plan belongs next to the thing it
    prices — this page exists for the two questions a product form can never
    answer: which discounts are running right now, and where the margin is
    thin. Both needed opening twelve forms before.
    """

    form = PlanForm
    list_display = ["plan", "price", "margin", "promo_window", "is_available"]
    list_filter = ["is_available", "product__category"]
    search_fields = ["title"]
    list_select_related = ["product"]
    list_fullwidth = True
    ordering = ["product__name", "sort_order"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        # A plan with no product is meaningless; it is added on the product.
        return False

    @display(description=_("پلن"), header=True, ordering="product__name")
    def plan(self, obj: Plan) -> list[Any]:
        mark = Thumbnail(obj.product.image.url) if obj.product.image else None
        return [obj.product.name, obj.title, obj.product.name[:1], mark]

    @admin.display(description=_("قیمت"), ordering="sale_price")
    def price(self, obj: Plan) -> str:
        effective = obj.effective_price
        if effective == obj.sale_price:
            return amount(effective)
        # The struck original matters here: this page is where a wrong
        # promotion is spotted.
        return format_html(
            '<s class="panel-was">{}</s> {}',
            format_toman(obj.sale_price, with_unit=False),
            amount(effective),
        )

    @admin.display(description=_("سود"))
    def margin(self, obj: Plan) -> str:
        """What the plan actually earns at the price it is selling for today.

        Routed through `effective_price` so a running discount is counted,
        rather than a second price rule. A loss is written as one, in the
        danger colour: as a badge it looked exactly like a profit, and a
        leading minus sign is moved to the far end by the bidi algorithm.
        """
        price = obj.effective_price
        if price <= 0:
            return "—"
        profit = price - obj.cost_price
        share = to_persian_digits(round(abs(profit) / price * 100))
        figure = f"{format_toman(abs(profit), with_unit=False)} تومان"
        if profit < 0:
            return format_html(
                '<span class="panel-loss">{} {} ({}٪)</span>', gettext("زیان"), figure, share
            )
        return format_html('<span class="panel-amount">{}</span> ({}٪)', figure, share)

    @admin.display(description=_("تخفیف"))
    def promo_window(self, obj: Plan) -> str:
        if not obj.promotion_is_active:
            return "—"
        left = obj.promo_seconds_left
        return countdown(left) if left is not None else gettext("بدون پایان")

    def get_search_results(
        self, request: HttpRequest, queryset: QuerySet[Plan], search_term: str
    ) -> tuple[QuerySet[Plan], bool]:
        term = search_term.strip()[:100]
        if not term:
            return queryset, False
        products = Product.objects.search(term).values("pk")
        return queryset.filter(Q(product__in=products) | Q(title__icontains=term)), False

    def construct_change_message(
        self, request: HttpRequest, form: Any, formsets: Any, add: bool = False
    ) -> list[dict[str, Any]]:
        message = super().construct_change_message(request, form, formsets, add)
        if not add:
            with_price_changes(message, form, inline=False)
        return message

    def save_model(self, request: HttpRequest, obj: Plan, form: Any, change: bool) -> None:
        super().save_model(request, obj, form, change)
        if change and (changes := price_changes(form)):
            # The product's history, where the plan's price is read from.
            with translation.override(None):
                entry = {"name": str(Plan._meta.verbose_name), "object": str(obj)}
            self.log_change(request, obj.product, [{"changed": {**entry, "fields": changes}}])
        warn_if_below_cost(request, obj)

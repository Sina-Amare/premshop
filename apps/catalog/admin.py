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
from datetime import timedelta
from decimal import Decimal
from typing import Any, ClassVar, cast

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Max, Q, QuerySet
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.utils import timezone, translation
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from unfold.admin import StackedInline, TabularInline
from unfold.decorators import display
from unfold.forms import PaginationInlineFormSet
from unfold.widgets import (
    UnfoldAdminSelectWidget,
    UnfoldAdminTextareaWidget,
    UnfoldAdminTextInputWidget,
)

from apps.catalog.admin_preview import PreviewAdminMixin
from apps.catalog.models import (
    NO_AVAILABLE_PLAN,
    PRICE_FIELDS,
    Category,
    PageRow,
    Plan,
    Product,
    ProductQuestion,
    ProductSection,
    ProductSpec,
    placeholders_in,
)
from apps.core.formatting import (
    format_countdown,
    format_jalali,
    format_toman,
    to_persian_digits,
)

# The two freshness lists. A toman price follows the exchange rate week by
# week; a provider's features and terms move more slowly.
STALE_PRICE_DAYS = 7
STALE_FACTS_DAYS = 30

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


class BrandColourWidget(UnfoldAdminTextInputWidget):
    def __init__(self, product: Product | None, attrs: Any = None) -> None:
        super().__init__(attrs)
        self.product = product

    def render(self, name: str, value: Any, attrs: Any = None, renderer: Any = None) -> SafeString:
        return super().render(name, value, attrs, renderer) + render_to_string(
            "admin/catalog/brand_colour.html", {"product": self.product}
        )


class DraggedRowForm(forms.ModelForm):
    """A row of a dragged inline, which a drag alone does not fill in."""

    def has_changed(self) -> bool:
        # Unfold numbers every row of the table on a drop, the blank extra
        # rows too. Counted as a change, the move made the formset demand a
        # title for each blank row, and every drag ended in a failed save.
        if self.instance.pk is None:
            return any(name != "sort_order" for name in self.changed_data)
        return super().has_changed()


class ProductSpecForm(DraggedRowForm):
    class Meta:
        model = ProductSpec
        fields = ["title", "value", "is_key", "sort_order"]
        widgets = {"title": SuggestingTextInput}


class ProductRowsFormSet(PaginationInlineFormSet):
    """The rows under one product on its form, checked as the submitted whole.

    Each row checks itself, its placeholders included (models.PageRow). What
    this adds is what only the rows together can say: going live needs an
    available plan, and a second «پیش از خرید» is refused, which the database
    refuses as well but cannot say so for two rows that are both new.
    """

    def clean(self) -> None:
        super().clean()
        rows = [form for form in self.forms if self.is_kept(form)]
        if self.model is ProductSection:
            kinds = [form.cleaned_data.get("kind") for form in rows]
            if kinds.count(ProductSection.Kind.BEFORE_BUYING) > 1:
                raise ValidationError(_("هر محصول حداکثر یک بخش «پیش از خرید» دارد."))
        product = self.instance
        if self.model is Plan and product.status == Product.Status.ACTIVE:
            if not product.is_live_in_db() and not any(form.instance.is_available for form in rows):
                raise ValidationError(NO_AVAILABLE_PLAN)

    def save_new(self, form: forms.ModelForm, commit: bool = True) -> Any:
        # A row added on the form cannot be dragged until it is saved, so it
        # arrives with the default order, which put it first. It goes last.
        if commit and "sort_order" not in form.changed_data:
            last = self.model._default_manager.filter(product=self.instance).aggregate(
                last=Max("sort_order")
            )["last"]
            form.instance.sort_order = 0 if last is None else last + 1
        return super().save_new(form, commit)

    def is_kept(self, form: forms.ModelForm) -> bool:
        """Whether `form` is a row that will exist once this formset is saved."""
        if getattr(form, "cleaned_data", {}).get("DELETE"):
            return False
        return form.instance.pk is not None or form.has_changed()


class ProductForm(forms.ModelForm):
    """The product's own fields; Product.clean() asks the publishing rule of them.

    The rows are asked by their own forms, which exist only for an inline this
    operator may change. Going live, the rows of any other inline are read as
    stored, or they would reach the page unasked.
    """

    unseen: ClassVar[list[type[PageRow]]] = []

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        product = self.instance
        if self.cleaned_data.get("status") == Product.Status.ACTIVE and not product.is_live_in_db():
            for model in self.unseen:
                for row in cast(Any, model)._default_manager.filter(product_id=product.pk):
                    for _field, message in placeholders_in(row):
                        # Above the form: under «وضعیت» it would take the
                        # status out of the instance, and the rest of the
                        # rule would not be asked in this save.
                        self.add_error(None, message)
        return cleaned


class ProductSpecInline(TabularInline):
    model = ProductSpec
    form = ProductSpecForm
    formset = ProductRowsFormSet
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

    stock_version = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["stock_version"] = signing.dumps(
                {"plan": self.instance.pk, "stock": self.instance.stock_quantity},
                salt="catalog.stock",
            )
        for name in ("promo_starts_at", "promo_ends_at"):
            if name not in self.fields:
                continue
            label = self.fields[name].label
            date, time = self.fields[name].widget.widgets
            date.attrs["aria-label"] = gettext("تاریخ %(label)s") % {"label": label}
            time.attrs["aria-label"] = gettext("ساعت %(label)s") % {"label": label}
            for box in (date, time):
                box.attrs["aria-describedby"] = f"{self[name].auto_id}_helptext"

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if self.instance.pk and connection.in_atomic_block:
            product_ids = set(
                Plan.objects.filter(pk=self.instance.pk).values_list("product_id", flat=True)
            )
            if product := cleaned.get("product"):
                product_ids.add(product.pk)
            list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by("pk"))
            current = Plan.objects.select_for_update().get(pk=self.instance.pk)
            if current.product_id not in product_ids:
                raise ValidationError("اطلاعات محصول تغییر کرده است. صفحه را دوباره باز کنید.")
            if "stock_quantity" in cleaned:
                try:
                    snapshot = signing.loads(cleaned.get("stock_version", ""), salt="catalog.stock")
                except signing.BadSignature:
                    raise ValidationError(
                        "صفحه‌ی ویرایش معتبر نیست. صفحه را دوباره باز کنید."
                    ) from None
                if (
                    not isinstance(snapshot, dict)
                    or snapshot.get("plan") != current.pk
                    or "stock" not in snapshot
                ):
                    raise ValidationError("صفحه‌ی ویرایش معتبر نیست. صفحه را دوباره باز کنید.")
                if cleaned["stock_quantity"] == snapshot["stock"]:
                    cleaned["stock_quantity"] = current.stock_quantity
                    self.changed_data[:] = [
                        name for name in self.changed_data if name != "stock_quantity"
                    ]
                elif current.stock_quantity != snapshot["stock"]:
                    self.add_error(
                        "stock_quantity",
                        "موجودی تغییر کرده است. صفحه را دوباره باز کنید و مقدار جدید را ببینید.",
                    )
            else:
                self.instance.stock_quantity = current.stock_quantity
        self.changed_data[:] = [name for name in self.changed_data if name != "stock_version"]
        return cleaned


@admin.display(description=_("وضعیت موجودی"))
def stock_levels(obj: Plan) -> str:
    if obj.stock_quantity is None:
        return gettext("بدون محدودیت موجودی")
    return gettext("%(available)s تا مانده") % {"available": to_persian_digits(obj.stock_quantity)}


class PlanInline(StackedInline):
    model = Plan
    form = PlanForm
    formset = ProductRowsFormSet
    extra = 1
    show_title = False
    ordering = ["sort_order", "id"]
    ordering_field = "sort_order"
    hide_ordering_field = True
    readonly_fields = ["stock_levels"]
    stock_levels = staticmethod(stock_levels)
    fieldsets = [
        (
            None,
            {
                "fields": [
                    ("title", "duration_days"),
                    ("cost_price", "sale_price"),
                    # A row of its own: Unfold hides the whole row that holds
                    # the ordering field, and while it shared one with title
                    # and duration no screen could add a plan.
                    "sort_order",
                ]
            },
        ),
        (
            _("موجودی و تعداد"),
            {
                "fields": [
                    "stock_version",
                    ("stock_quantity", "max_quantity"),
                    "stock_levels",
                    "is_available",
                ]
            },
        ),
        (_("تخفیف"), {"fields": ["promo_price", ("promo_starts_at", "promo_ends_at")]}),
        (
            _("اطلاعات مشتری و تأمین"),
            {
                "fields": [
                    ("input_type", "telegram_required"),
                    "customer_input_label",
                    "supplier_url",
                ]
            },
        ),
    ]


class ProductSectionForm(DraggedRowForm):
    class Meta:
        model = ProductSection
        fields = ["kind", "title", "body", "image", "image_alt", "sort_order"]


class ProductSectionInline(StackedInline):
    """The page's prose, one row per section, in the order the operator drags.

    No row until one is added: a product needs only the kinds that suit it,
    and the guide above the rows says what each kind is for.
    """

    model = ProductSection
    form = ProductSectionForm
    formset = ProductRowsFormSet
    extra = 0
    show_title = False
    ordering = ["sort_order", "id"]
    ordering_field = "sort_order"
    hide_ordering_field = True
    template_before = "admin/catalog/product/section_guide.html"
    # `sort_order` on a row of its own, as on PlanInline: Unfold hides the row
    # that holds the ordering field.
    fields = ["kind", "title", "body", ("image", "image_alt"), "sort_order"]

    def formfield_for_choice_field(self, db_field: Any, request: HttpRequest, **kwargs: Any) -> Any:
        if db_field.name == "kind":
            # A blank first, as on delivery_type: with a kind chosen in
            # advance, a row added and left empty would save as a section.
            kwargs["choices"] = db_field.get_choices(
                include_blank=True, blank_choice=[("", _("Select value"))]
            )
        return super().formfield_for_choice_field(db_field, request, **kwargs)


class ProductQuestionForm(DraggedRowForm):
    class Meta:
        model = ProductQuestion
        fields = ["question", "answer", "sort_order"]
        # An answer is a sentence or two; the default box is ten lines.
        widgets = {"answer": UnfoldAdminTextareaWidget(attrs={"rows": 4})}


class ProductQuestionInline(StackedInline):
    model = ProductQuestion
    form = ProductQuestionForm
    formset = ProductRowsFormSet
    extra = 0
    show_title = False
    ordering = ["sort_order", "id"]
    ordering_field = "sort_order"
    hide_ordering_field = True
    fields = ["question", "answer", "sort_order"]


class StaleFactsFilter(admin.SimpleListFilter):
    title = _("نیاز به بررسی")
    parameter_name = "facts"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, Any]]:
        return [
            ("stale", _("حساس به زمان، بیش از ۳۰ روز بررسی‌نشده")),
            ("no_notes", _("منتشرشده، بدون نکات استفاده")),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Product]) -> QuerySet[Product]:
        if self.value() == "no_notes":
            # Live before publishing asked for it; taken down, it cannot go back up.
            return queryset.filter(status=Product.Status.ACTIVE, usage_notes="")
        if self.value() != "stale":
            return queryset
        cutoff = timezone.localdate() - timedelta(days=STALE_FACTS_DAYS)
        return queryset.filter(time_sensitive=True).filter(
            Q(facts_checked_on__isnull=True) | Q(facts_checked_on__lt=cutoff)
        )


class StalePriceFilter(admin.SimpleListFilter):
    title = _("تأیید قیمت")
    parameter_name = "price_checked"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, Any]]:
        return [("stale", _("بیش از ۷ روز پیش، یا هرگز"))]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Plan]) -> QuerySet[Plan]:
        if self.value() != "stale":
            return queryset
        cutoff = timezone.now() - timedelta(days=STALE_PRICE_DAYS)
        return queryset.filter(
            Q(price_confirmed_at__isnull=True) | Q(price_confirmed_at__lt=cutoff)
        )


class CopySectionsForm(forms.Form):
    source = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        label=_("از محصول"),
        widget=UnfoldAdminSelectWidget,
        empty_label=_("Select value"),
    )

    def __init__(self, *args: Any, targets: list[Product], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        others = Product.objects.exclude(pk__in=[target.pk for target in targets])
        self.fields["source"].queryset = (  # type: ignore[attr-defined]
            others.filter(sections__isnull=False).distinct().order_by("name")
        )


def copy_sections_onto(source: Product, target: Product) -> tuple[list[ProductSection], bool]:
    """Every section of `source` as new rows at the end of `target`.

    A copy is a row of its own, so editing it never changes the page it came
    from. Its image is the same stored file, which nothing deletes; a new
    upload on either row is a new file. A «پیش از خرید» is left out when
    `target` already has one, and the second value says so.
    """
    existing = target.sections.all()
    has_before_buying = existing.filter(kind=ProductSection.Kind.BEFORE_BUYING).exists()
    start = (existing.aggregate(last=Max("sort_order"))["last"] or 0) + 1
    copies = [
        ProductSection(
            product=target,
            kind=section.kind,
            title=section.title,
            body=section.body,
            image=section.image.name,
            image_alt=section.image_alt,
            sort_order=start + position,
        )
        for position, section in enumerate(source.sections.all())
        if not (has_before_buying and section.kind == ProductSection.Kind.BEFORE_BUYING)
    ]
    skipped = len(copies) < source.sections.count()
    return ProductSection.objects.bulk_create(copies), skipped


def refusal_reasons(form: forms.ModelForm, formsets: list[Any]) -> list[str]:
    """Every message a refused product form put under a field or a row, in order.

    The line under «وضعیت» is left out: it is the summary this list explains.
    """
    reasons = [str(m) for name, errors in form.errors.items() if name != "status" for m in errors]
    for formset in formsets:
        reasons += [str(m) for m in formset.non_form_errors()]
        reasons += [str(m) for row in formset.errors for errors in row.values() for m in errors]
    return reasons


def reasons_message(headline: str, problems: list[str]) -> SafeString:
    # A list, as on the form: run together, they were a paragraph nobody scans.
    reasons = format_html_join("", "<li>{}</li>", ((problem,) for problem in problems))
    return format_html('{}<ul class="panel-reasons">{}</ul>', headline, reasons)


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
class CategoryAdmin(PreviewAdminMixin):
    list_display = ["name", "slug", "product_count"]
    # Dragged like specs and plans, where the number box was a full-width
    # input with no label of its own. Unfold adds the hidden field.
    ordering_field = "sort_order"
    hide_ordering_field = True
    # The drag is the order. With the model's name tiebreak the header
    # showed a Latin «2», the sort priority of the name column.
    ordering = ["sort_order"]
    search_fields = ["name"]
    # `intro_html` is off the form: 0020 converted it into `intro`, and the
    # column stays only so that migration can be reversed.
    fields = [
        "name",
        "slug",
        "description",
        "intro",
        "sort_order",
        "seo_title",
        "seo_description",
    ]

    @admin.display(description=_("تعداد محصول"))
    def product_count(self, obj: Category) -> str:
        return to_persian_digits(obj.products.count())


@admin.register(Product)
class ProductAdmin(PreviewAdminMixin):
    list_display = [
        "product",
        "status_label",
        "price_range",
        "promo_left",
        "facts_checked",
        "updated",
    ]
    # Warranty and delivery type are not columns, so these are how to find them.
    list_filter = ["status", "category", "warranty", "delivery_type", StaleFactsFilter]
    actions = ["publish", "unpublish", "copy_sections", "delete_selected"]
    form = ProductForm
    # Only switches the search box on: get_search_results asks the shop's own
    # search, which ignores these fields.
    search_fields = ["name"]
    list_select_related = ["category"]
    inlines = [ProductSpecInline, PlanInline, ProductSectionInline, ProductQuestionInline]
    # The table is the operator's working surface; the centred container wasted
    # a third of a wide screen on margins.
    list_fullwidth = True
    # The product form is long enough to lose work in. The browser asks before
    # a half-finished product is abandoned.
    warn_unsaved_form = True

    # What still stands between a draft and publishing, at the top of its form.
    change_form_before_template = "admin/catalog/product/readiness.html"
    fieldsets = [
        (
            _("مشخصات اصلی"),
            {"fields": ["name", "slug", "category", "status", "image", "brand_colour"]},
        ),
        (
            _("تحویل و گارانتی"),
            {
                "fields": [
                    "delivery_type",
                    ("warranty", "renewable"),
                    ("delivery_hours", "delivery_note"),
                    "delivery_template",
                ]
            },
        ),
        (_("توضیحات محصول"), {"fields": ["short_description", "usage_notes"]}),
        (
            _("شرایط و بررسی اطلاعات"),
            {
                "fields": [
                    "refund_note",
                    ("time_sensitive", "facts_checked_on"),
                    "sources",
                ]
            },
        ),
        (_("نام‌های جستجو"), {"fields": ["aliases"], "classes": ["collapse"]}),
        (
            _("عنوان و توضیح سئو"),
            {"fields": ["seo_title", "seo_description"], "classes": ["collapse"]},
        ),
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
        """Publish each selected product the publishing rule allows, and name
        every reason for each one it does not."""
        ready = []
        drafts = queryset.exclude(status=Product.Status.ACTIVE).prefetch_related(
            "specs", "plans", "sections", "questions"
        )
        for product in drafts:
            if problems := product.publish_problems():
                headline = gettext("«%(product)s» منتشر نشد.") % {
                    "product": f"\u2068{product}\u2069"
                }
                messages.error(request, reasons_message(headline, problems))
            else:
                ready.append(product.pk)
        # When every product was refused, the reasons are the whole answer: a
        # green «۰ محصول فعال شد.» under them read as if one had gone through.
        if ready or not drafts:
            self._set_status(request, queryset.filter(pk__in=ready), Product.Status.ACTIVE)

    @admin.action(description=_("برداشتن از سایت"), permissions=["change"])
    def unpublish(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        """Take the selected products down, and name what each one lacks
        before the publishing rule lets it back up: a live product is exempt
        from the rule only while it stays live (ADR-0028)."""
        taken = list(
            queryset.exclude(status=Product.Status.DRAFT).prefetch_related(
                "specs", "plans", "sections", "questions"
            )
        )
        self._set_status(request, queryset, Product.Status.DRAFT)
        for product in taken:
            if problems := product.publish_problems():
                headline = gettext("«%(product)s» تا این‌ها درست نشود، دوباره منتشر نمی‌شود.") % {
                    "product": f"\u2068{product}\u2069"
                }
                messages.info(request, reasons_message(headline, problems))

    def has_copy_sections_permission(self, request: HttpRequest) -> bool:
        return self.has_change_permission(request) and request.user.has_perm(
            "catalog.add_productsection"
        )

    @admin.action(description=_("کپی بخش‌ها از محصول دیگر"), permissions=["copy_sections"])
    def copy_sections(
        self, request: HttpRequest, queryset: QuerySet[Product]
    ) -> TemplateResponse | None:
        """Ask which product to copy from, then add its sections to each one
        selected. The question is a page of its own, posted like the action.

        Never onto a live product: the copies are written straight to the
        page, past the publishing rule, and a draft's sections carry the
        placeholders it is waiting on.
        """
        targets = []
        for target in queryset:
            if target.status != Product.Status.ACTIVE:
                targets.append(target)
                continue
            messages.error(
                request,
                gettext(
                    "بخش‌ها روی «%(target)s» کپی نشد، چون منتشر شده است. اول آن را از سایت بردارید."
                )
                % {"target": f"\u2068{target}\u2069"},
            )
        if not targets:
            return None
        form = CopySectionsForm(request.POST if "source" in request.POST else None, targets=targets)
        if not form.is_valid():
            context = {
                **self.admin_site.each_context(request),
                "title": _("کپی بخش‌ها از محصول دیگر"),
                "form": form,
                "targets": targets,
                "opts": self.model._meta,
            }
            return TemplateResponse(request, "admin/catalog/product/copy_sections.html", context)
        source = form.cleaned_data["source"]
        notices = []
        with transaction.atomic():
            for target in targets:
                copies, skipped = copy_sections_onto(source, target)
                with translation.override(None):
                    kind = str(ProductSection._meta.verbose_name)
                    added = [{"added": {"name": kind, "object": str(copy)}} for copy in copies]
                self.log_change(request, target, added)
                names = {
                    "count": to_persian_digits(len(copies)),
                    "source": f"\u2068{source}\u2069",
                    "target": f"\u2068{target}\u2069",
                }
                message = gettext("%(count)s بخش از «%(source)s» به «%(target)s» کپی شد.") % names
                if skipped:
                    message += " " + gettext(
                        "«پیش از خرید» کپی نشد، چون «%(target)s» یکی دارد."
                    ) % {"target": names["target"]}
                notices.append(message)
        for message in notices:
            messages.success(request, message)
        return None

    @transaction.atomic
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

    @admin.display(description=_("آخرین بررسی"), ordering="facts_checked_on")
    def facts_checked(self, obj: Product) -> str:
        if obj.facts_checked_on is None:
            return "—"
        return format_html(
            '<span class="panel-date">{}</span>', format_jalali(obj.facts_checked_on)
        )

    @admin.display(description=_("به‌روزرسانی"), ordering="updated_at")
    def updated(self, obj: Product) -> str:
        """Jalali, like every other date this shop shows. The admin was
        printing «۱۸ اکتبر ۲۰۲۶» at an operator who works in Mehr and Aban —
        a Gregorian month name is a date they have to convert in their head."""
        return format_html('<span class="panel-date">{}</span>', format_jalali(obj.updated_at))

    def get_deleted_objects(self, objs: Any, request: HttpRequest) -> Any:
        if request.method == "POST":
            ids = [obj.pk for obj in objs]
            list(Product.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
            list(Plan.objects.select_for_update().filter(product_id__in=ids).order_by("pk"))
        return super().get_deleted_objects(objs, request)

    @admin.action(permissions=["delete"], description=_("حذف محصولات انتخاب‌شده"))
    def delete_selected(self, request: HttpRequest, queryset: QuerySet[Product]) -> Any:
        # Django's bulk action otherwise checks protection before its transaction.
        with transaction.atomic():
            return delete_selected(self, request, queryset)

    def get_queryset(self, request: Any) -> Any:
        return super().get_queryset(request).prefetch_related("plans")

    def get_readonly_fields(self, request: HttpRequest, obj: Product | None = None) -> Any:
        fields = super().get_readonly_fields(request, obj)
        return [*fields, "slug"] if obj and obj.status != Product.Status.DRAFT else fields

    def get_form(self, request: Any, obj: Any = None, change: bool = False, **kwargs: Any) -> Any:
        if request.method == "POST" and obj and connection.in_atomic_block:
            Product.objects.select_for_update().get(pk=obj.pk)
            # Checkout locks products before plans, each group in primary-key order.
            list(
                Plan.objects.select_for_update()
                .filter(product=obj)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
        form = super().get_form(request, obj, change=change, **kwargs)
        changeable = {
            inline.model
            for inline in self.get_inline_instances(request, obj)
            if inline.has_change_permission(request, obj)
        }
        inlines = self.get_inlines(request, obj)
        form.unseen = [inline.model for inline in inlines if inline.model not in changeable]
        if "brand_colour" in form.base_fields:
            field = form.base_fields["brand_colour"]
            field.widget = BrandColourWidget(obj, attrs=field.widget.attrs)
        if "status" in form.base_fields:
            form.base_fields["status"].help_text = _(
                "پیش‌نویس پنهان است. ناموجود، اگر آماده‌ی انتشار باشد، صفحه دارد ولی قابل خرید نیست."
            )
        return form

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Product | None = None,
    ) -> Any:
        """The readiness list at the top of the form (readiness.html).

        For a saved draft, what it still lacks; for a live product, what it
        would need to go back up once taken down; after a refused «فعال»,
        every reason the form gave, which otherwise stand under fields far apart.
        """
        form = context["adminform"].form
        if not form.is_bound and obj and obj.is_public:
            context["readiness"] = obj.publish_problems() or None
            context["readiness_live"] = True
        elif not form.is_bound:
            context["readiness"] = obj.publish_problems() if obj else None
            context["readiness_unavailable"] = obj and obj.status == Product.Status.UNAVAILABLE
        elif form.data.get("status") == Product.Status.ACTIVE and not (obj and obj.is_live_in_db()):
            formsets = [inline.formset for inline in context["inline_admin_formsets"]]
            context["readiness"] = refusal_reasons(form, formsets) or None
        return super().render_change_form(request, context, add, change, form_url, obj)

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

    def formfield_for_dbfield(self, db_field: Any, request: HttpRequest, **kwargs: Any) -> Any:
        if db_field.name == "sources":
            # A few addresses, not prose: the default box is ten lines tall.
            kwargs["widget"] = UnfoldAdminTextareaWidget(attrs={"rows": 4})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

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
class PlanAdmin(PreviewAdminMixin):
    """Prices, stock and availability across the catalogue."""

    form = PlanForm
    list_display = [
        "plan",
        "price",
        "margin",
        "stock_levels",
        "promo_window",
        "price_checked",
        "is_available",
    ]
    readonly_fields = ["stock_levels"]
    stock_levels = staticmethod(stock_levels)
    fieldsets = [
        (None, {"fields": ["product", ("title", "duration_days")]}),
        (_("قیمت"), {"fields": [("cost_price", "sale_price")]}),
        (_("تخفیف"), {"fields": ["promo_price", ("promo_starts_at", "promo_ends_at")]}),
        (
            _("موجودی و تعداد"),
            {
                "fields": [
                    "stock_version",
                    ("stock_quantity", "max_quantity"),
                    "stock_levels",
                    "is_available",
                ]
            },
        ),
        (
            _("اطلاعات مشتری و تأمین"),
            {"fields": ["input_type", "telegram_required", "customer_input_label", "supplier_url"]},
        ),
        (_("ترتیب نمایش"), {"fields": ["sort_order"]}),
    ]
    list_filter = ["is_available", "product__category", StalePriceFilter]
    actions = ["confirm_prices"]
    search_fields = ["title"]
    list_select_related = ["product"]
    list_fullwidth = True
    ordering = ["product__name", "sort_order"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        # A plan with no product is meaningless; it is added on the product.
        return False

    @admin.action(description=_("قیمت‌ها را امروز بررسی کردم"), permissions=["change"])
    def confirm_prices(self, request: HttpRequest, queryset: QuerySet[Plan]) -> None:
        """The prices were compared with today's supplier price and rate and still
        hold. Nothing else about the plans changes, so their history does not either."""
        count = queryset.update(price_confirmed_at=timezone.now())
        messages.success(
            request,
            gettext("قیمت %(count)s پلن تأیید شد.") % {"count": to_persian_digits(count)},
        )

    @admin.display(description=_("تأیید قیمت"), ordering="price_confirmed_at")
    def price_checked(self, obj: Plan) -> str:
        if obj.price_confirmed_at is None:
            return "—"
        return format_html(
            '<span class="panel-date">{}</span>', format_jalali(obj.price_confirmed_at)
        )

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

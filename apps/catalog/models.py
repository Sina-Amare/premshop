"""The catalog: what is on the shelf, and at what price.

Four tables, flat. A Category is a shelf; a Product is a thing on it; a Plan is
one way to buy that thing («۱ ماهه», «۳ ماهه») with its own price; a ProductSpec
is one row of the product's fact table. No category tree and no JSON specs —
both were considered and cut (ADR-0014): a few dozen products do not need a
hierarchy, and a spec you cannot query or edit in admin is not worth storing.

Money is toman, whole units, as a DecimalField with no decimal places: there is
no fractional toman, and Decimal keeps every arithmetic exact all the way to the
order snapshot. The same definition is used by every money column in the project.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.search import normalize_for_search


def MoneyField(**kwargs: Any) -> models.DecimalField:  # noqa: N802 — reads as a type
    """Toman, whole units. One definition so every money column agrees."""
    return models.DecimalField(max_digits=12, decimal_places=0, **kwargs)


class Category(models.Model):
    name = models.CharField("نام", max_length=100)
    slug = models.SlugField("نامک", max_length=100, unique=True, allow_unicode=True)
    description = models.CharField("توضیح کوتاه", max_length=300, blank=True, default="")
    intro_html = models.TextField("متن معرفی", blank=True, default="")
    seo_title = models.CharField("عنوان سئو", max_length=70, blank=True, default="")
    seo_description = models.CharField("توضیح سئو", max_length=160, blank=True, default="")
    sort_order = models.SmallIntegerField("ترتیب", default=0)

    class Meta:
        verbose_name = "دسته‌بندی"
        verbose_name_plural = "دسته‌بندی‌ها"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)


class ProductQuerySet(models.QuerySet["Product"]):
    def public(self) -> ProductQuerySet:
        """What a visitor may see. Draft and unavailable products never leak
        through a URL, a search, or a category listing — one filter, used by
        every public view."""
        return self.filter(status=Product.Status.ACTIVE)

    def search(self, query: str) -> ProductQuerySet:
        """Match on the normalised column so an Arabic-keyboard «كلاود» finds
        «کلاود». Deliberately icontains and deliberately unindexed: a btree cannot
        serve a substring match, and a sequential scan over a few dozen rows is
        instant. pg_trgm is the named upgrade if the catalog ever grows."""
        needle = normalize_for_search(query)
        if not needle:
            return self.none()
        return self.filter(search_text__icontains=needle)


class Product(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        ACTIVE = "active", "فعال"
        UNAVAILABLE = "unavailable", "ناموجود"

    class DeliveryType(models.TextChoices):
        READY_ACCOUNT = "ready_account", "اکانت آماده"
        ON_CUSTOMER_ACCOUNT = "on_customer_account", "روی اکانت خود مشتری"
        CODE_LICENSE = "code_license", "کد / لایسنس"
        GIFT_CARD = "gift_card", "گیفت کارت"

    class Region(models.TextChoices):
        # The owner's ruling names Iran; the others exist so a Turkey-region
        # account can be labelled honestly rather than mislabelled «جهانی».
        GLOBAL = "global", "جهانی"
        IRAN = "ir", "ایران"
        US = "us", "آمریکا"
        EU = "eu", "اروپا"
        TURKEY = "tr", "ترکیه"

    class Warranty(models.TextChoices):
        NONE = "none", "بدون گارانتی"
        DAYS_7 = "days_7", "۷ روز"
        FULL_PERIOD = "full_period", "تا پایان دوره"

    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products", verbose_name="دسته‌بندی"
    )
    name = models.CharField("نام", max_length=150)
    slug = models.SlugField("نامک", max_length=150, unique=True, allow_unicode=True)
    short_description = models.CharField("توضیح کوتاه", max_length=300, blank=True, default="")
    full_description = models.TextField("توضیح کامل", blank=True, default="")
    image = models.ImageField("تصویر", upload_to="products/", null=True, blank=True)
    delivery_type = models.CharField("نوع تحویل", max_length=24, choices=DeliveryType.choices)
    region = models.CharField("ریجن", max_length=24, choices=Region.choices, default=Region.GLOBAL)
    warranty = models.CharField(
        "گارانتی", max_length=24, choices=Warranty.choices, default=Warranty.NONE
    )
    delivery_hours = models.SmallIntegerField("مهلت تحویل (ساعت)", default=24)
    delivery_template = models.TextField("قالب تحویل", blank=True, default="")
    status = models.CharField("وضعیت", max_length=16, choices=Status.choices, default=Status.DRAFT)
    # Maintained on save, never edited by hand. See search.py for what it folds.
    search_text = models.TextField(editable=False, blank=True, default="")
    seo_title = models.CharField("عنوان سئو", max_length=70, blank=True, default="")
    seo_description = models.CharField("توضیح سئو", max_length=160, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[ProductQuerySet] = ProductQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        verbose_name = "محصول"
        verbose_name_plural = "محصولات"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=True)
        self.search_text = normalize_for_search(f"{self.name} {self.short_description}")
        super().save(*args, **kwargs)

    @property
    def is_public(self) -> bool:
        return self.status == self.Status.ACTIVE

    def available_plans(self) -> list[Plan]:
        """Iterates plans.all() so a prefetch is honoured — a card grid of thirty
        products must not become thirty queries."""
        return [plan for plan in self.plans.all() if plan.is_available]

    @property
    def cheapest_plan(self) -> Plan | None:
        plans = self.available_plans()
        return min(plans, key=lambda plan: plan.effective_price) if plans else None


class ProductSpec(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="specs")
    title = models.CharField("عنوان", max_length=100)
    value = models.CharField("مقدار", max_length=255)
    sort_order = models.SmallIntegerField("ترتیب", default=0)

    class Meta:
        verbose_name = "مشخصه"
        verbose_name_plural = "مشخصات"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.title}: {self.value}"


class Plan(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="plans")
    title = models.CharField("عنوان", max_length=100)
    duration_days = models.IntegerField("مدت (روز)", null=True, blank=True)  # null: never expires
    cost_price = MoneyField(verbose_name="قیمت خرید")  # operator-only; snapshotted at order time
    sale_price = MoneyField(verbose_name="قیمت فروش")
    promo_price = MoneyField(verbose_name="قیمت تخفیفی", null=True, blank=True)
    promo_starts_at = models.DateTimeField("شروع تخفیف", null=True, blank=True)
    promo_ends_at = models.DateTimeField("پایان تخفیف", null=True, blank=True)
    is_available = models.BooleanField(
        "موجود", default=True
    )  # soft delete: sold plans are never removed
    requires_customer_input = models.BooleanField("نیاز به اطلاعات مشتری", default=False)
    customer_input_label = models.CharField(
        "برچسب اطلاعات مشتری", max_length=200, blank=True, default=""
    )
    supplier_url = models.URLField("لینک تأمین‌کننده", blank=True, default="")
    sort_order = models.SmallIntegerField("ترتیب", default=0)

    class Meta:
        verbose_name = "پلن"
        verbose_name_plural = "پلن‌ها"
        ordering = ["sort_order", "id"]
        constraints = [
            # A promotion is a lower price or it is nothing. Zero, negative, or
            # at-or-above the list price is refused by the database itself, so
            # no admin form, shell or import can put the shop in a state where
            # the struck-through "old" price is lower than the "new" one.
            models.CheckConstraint(
                condition=Q(promo_price__isnull=True)
                | (Q(promo_price__gt=0) & Q(promo_price__lt=F("sale_price"))),
                name="plan_promo_price_below_sale_price",
                violation_error_message="قیمت تخفیفی باید بیشتر از صفر و کمتر از قیمت فروش باشد.",
            ),
            models.CheckConstraint(
                condition=Q(promo_starts_at__isnull=True)
                | Q(promo_ends_at__isnull=True)
                | Q(promo_starts_at__lt=F("promo_ends_at")),
                name="plan_promo_window_starts_before_it_ends",
                violation_error_message="شروع تخفیف باید قبل از پایان آن باشد.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} — {self.title}"

    @property
    def effective_price(self) -> Decimal:
        from apps.catalog.pricing import effective_price

        return effective_price(self)

    @property
    def promotion_is_active(self) -> bool:
        return self.effective_price != self.sale_price

    @property
    def expires_never(self) -> bool:
        return self.duration_days is None

    def promo_window_open_at(self, at: datetime | None = None) -> bool:
        """Whether a promotion applies at `at`. An absent bound is open-ended."""
        if self.promo_price is None:
            return False
        moment = at or timezone.now()
        if self.promo_starts_at and moment < self.promo_starts_at:
            return False
        if self.promo_ends_at and moment >= self.promo_ends_at:
            return False
        return True

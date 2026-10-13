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
        «کلاود», and match each word separately so word order and an extra word
        do not decide the answer — «جیپت چت» and «چت جیپت پلاس» are the same
        question. What a word can match is widened by `aliases`, because no
        folding turns one transliteration into another.

        Deliberately icontains and deliberately unindexed: a btree cannot serve a
        substring match, and a sequential scan over a few dozen rows is instant.
        pg_trgm is the named upgrade if the catalog ever grows."""
        needle = normalize_for_search(query)
        if not needle:
            return self.none()
        matches = self
        # Bounded: a 100-character query is already capped by the view, and no
        # real search needs more than a handful of words to be precise.
        for word in needle.split()[:8]:
            matches = matches.filter(search_text__icontains=word)
        return matches


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
    # Search-only, never rendered: the spellings a customer actually types. A
    # Persian shopper writes «چت جیپت» for ChatGPT, and no amount of character
    # folding turns one transliteration into another — only this list does.
    aliases = models.TextField(
        "املاهای دیگر",
        blank=True,
        default="",
        help_text="هر املایی که مشتری ممکن است بنویسد، با ویرگول: چت جی پی تی، چت جیپت، gpt",
    )
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
        constraints = [
            # The 48-hour cap is the delivery promise on every product page
            # (ADR-0009): zero would read as instant, and 49 breaks the promise.
            models.CheckConstraint(
                condition=Q(delivery_hours__gte=1) & Q(delivery_hours__lte=48),
                name="product_delivery_hours_1_to_48",
                violation_error_message="مهلت تحویل باید بین ۱ تا ۴۸ ساعت باشد.",
            ),
            # `choices=` binds only the admin form; the database binds everything.
            models.CheckConstraint(
                condition=Q(status__in=["draft", "active", "unavailable"]),
                name="product_status_valid",
                violation_error_message="وضعیت محصول معتبر نیست.",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=True)
        self.search_text = normalize_for_search(
            f"{self.name} {self.short_description} {self.aliases}"
        )
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
    # Default 1, so a quantity control appears only where the operator has
    # said it makes sense. Most of this shop is subscriptions activated on one
    # person's account, where "how many" is not a question the buyer has — and
    # a stepper on every product invites an order nobody can deliver. Gift
    # cards are the case that wants it.
    max_quantity = models.PositiveSmallIntegerField(
        "حداکثر تعداد در هر سفارش",
        default=1,
        help_text="۱ یعنی انتخاب تعداد اصلاً نمایش داده نمی‌شود. برای گیفت کارت بیشتر کنید.",
    )
    requires_customer_input = models.BooleanField(
        "نیاز به اطلاعات مشتری",
        default=False,
        help_text="اگر برای فعال‌سازی چیزی از مشتری لازم است (ایمیل، نام کاربری).",
    )
    customer_input_label = models.CharField(
        "چه چیزی از مشتری بپرسیم؟",
        max_length=200,
        blank=True,
        default="",
        help_text="عیناً همین جمله به مشتری نشان داده می‌شود؛ مثلاً «ایمیل حساب Spotify شما».",
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
            # Zero stays allowed — data-model §3 says `>= 0`, and whether a
            # zero-priced plan may be sold is a checkout question for S4b.
            models.CheckConstraint(
                condition=Q(cost_price__gte=0) & Q(sale_price__gte=0),
                name="plan_prices_not_negative",
                violation_error_message="قیمت خرید و قیمت فروش نمی‌توانند منفی باشند.",
            ),
            # NULL means the plan never expires; zero or less would date the expiry
            # on or before delivery, and the renewal reminder would fire at once.
            models.CheckConstraint(
                condition=Q(duration_days__isnull=True) | Q(duration_days__gt=0),
                name="plan_duration_positive_or_unlimited",
                violation_error_message="مدت پلن باید دست‌کم یک روز باشد، یا خالی برای بی‌پایان.",
            ),
            models.CheckConstraint(
                condition=Q(max_quantity__gte=1) & Q(max_quantity__lte=10),
                name="plan_max_quantity_1_to_10",
                violation_error_message="حداکثر تعداد باید بین ۱ تا ۱۰ باشد.",
            ),
            # One input per credential. A plan that needs the customer's own
            # account details cannot be ordered twice on one line: there is
            # one box to type the email into, so a quantity of two would mean
            # two deliveries to one address with no way to say so. The buy box
            # has always enforced this in JavaScript; here it cannot be got
            # around by the admin, a shell or an import.
            models.CheckConstraint(
                condition=Q(requires_customer_input=False) | Q(max_quantity=1),
                name="plan_customer_input_is_one_per_order",
                violation_error_message=(
                    "پلنی که به اطلاعات مشتری نیاز دارد، در هر سفارش فقط یکی قابل خرید است."
                ),
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

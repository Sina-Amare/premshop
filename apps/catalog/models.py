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

import re
from collections.abc import Collection
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import TrigramWordSimilarity
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import validate_slug
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.catalog.search import normalize_for_search
from apps.core.formatting import to_persian_digits

# How close a misspelling has to be before the shop offers it as the answer.
# Measured against every seeded product: real misspellings of a product name
# score 0.5 and up, and the highest any unrelated word reaches is well under
# this. Raise it if a search starts answering the wrong question.
FUZZY_THRESHOLD = 0.4

# A Persian or Arabic letter. A name with one has no Latin slug to give.
_PERSIAN = re.compile(r"[\u0600-\u06ff]")


def MoneyField(**kwargs: Any) -> models.DecimalField:  # noqa: N802 — reads as a type
    """Toman, whole units. One definition so every money column agrees."""
    return models.DecimalField(max_digits=12, decimal_places=0, **kwargs)


def latin_slug(name: str) -> str:
    """The slug a name gives on its own, or "" when it is not a Latin name.

    Slugs are Latin: a Persian slug is percent-encoded into a line of
    %D8%A7 wherever the address is pasted.
    """
    return "" if _PERSIAN.search(name) else slugify(name)


def slug_for_save(name: str) -> str:
    """latin_slug, for a save with no slug given. An empty slug reverses to no
    page, and one category with it broke the header on every page."""
    if slug := latin_slug(name):
        return slug
    raise ValueError(f"{name!r} gives no Latin slug; set the slug explicitly")


def check_slug_is_latin(row: Category | Product) -> None:
    """A new or changed slug must be Latin.

    The field still accepts a Persian slug saved before slugs were Latin, and
    such a slug stays until someone changes it: rewriting it moves an address
    that links already point at, and refusing it made the row unsaveable.
    """
    if not row.slug:
        return  # the field's own "required", or clean() fills it
    try:
        validate_slug(row.slug)
    except ValidationError as error:
        manager = type(row)._default_manager
        if row.pk is None or not manager.filter(pk=row.pk, slug=row.slug).exists():
            raise ValidationError({"slug": error.error_list}) from None


class ChecksNameTheirField:
    """File each CHECK's message under the field it is about.

    A CheckConstraint names no field, so Django reports its violation above
    the form. Unfold's stacked inline prints nothing there, and a promotion
    above the price came back as an English banner with nothing below it to
    correct.
    """

    CHECK_FIELDS: ClassVar[dict[str, str]] = {}

    def validate_constraints(self, exclude: Collection[str] | None = None) -> None:
        errors: dict[str, list[ValidationError]] = {}
        for constraint in self._meta.constraints:  # type: ignore[attr-defined]
            try:
                constraint.validate(type(self), self, exclude=exclude)
            except ValidationError as error:
                field = self.CHECK_FIELDS.get(constraint.name, NON_FIELD_ERRORS)
                errors.setdefault(field, []).append(error)
        if errors:
            raise ValidationError(errors)


class Category(models.Model):
    name = models.CharField(_("نام"), max_length=100)
    # allow_unicode for the slugs saved before slugs were Latin; a new or
    # changed slug is held to Latin by clean() (check_slug_is_latin).
    slug = models.SlugField(
        _("نامک"),
        max_length=100,
        unique=True,
        allow_unicode=True,
        help_text=_("در نشانی صفحه می‌آید و لاتین است، مثل «ai»."),
    )
    description = models.CharField(
        _("توضیح کوتاه"),
        max_length=300,
        blank=True,
        default="",
    )
    intro_html = models.TextField(
        _("متن معرفی"),
        blank=True,
        default="",
        help_text=_("برچسب HTML اجرا نمی‌شود و همان‌طور که نوشته شده دیده می‌شود."),
    )
    seo_title = models.CharField(
        _("عنوان سئو"),
        max_length=70,
        blank=True,
        default="",
    )
    seo_description = models.CharField(
        _("توضیح سئو"),
        max_length=160,
        blank=True,
        default="",
    )
    sort_order = models.SmallIntegerField(
        _("ترتیب نمایش"), default=0, help_text=_("عدد کوچک‌تر، بالاتر.")
    )

    class Meta:
        verbose_name = _("دسته‌بندی")
        verbose_name_plural = _("دسته‌بندی‌ها")
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slug_for_save(self.name)
        super().save(*args, **kwargs)

    def clean(self) -> None:
        check_slug_is_latin(self)


class ProductQuerySet(models.QuerySet["Product"]):
    def public(self) -> ProductQuerySet:
        """What a visitor may see. Draft and unavailable products never leak
        through a URL, a search, or a category listing — one filter, used by
        every public view."""
        return self.filter(status=Product.Status.ACTIVE)

    def search(self, query: str) -> ProductQuerySet:
        """Exact first, then close enough.

        The first pass matches on the normalised column, so an Arabic-keyboard
        «كلاود» finds «کلاود», and matches each word separately, so word order
        and an extra word do not decide the answer — «جیپت چت» and «چت جیپت
        پلاس» are the same question. What a word can mean is widened by
        `aliases`, the operator's list of spellings a customer might type.

        That list can never be finished, which is the whole reason for the
        second pass. «چت جیپت» was on it and «چت جپت» was not, so one real
        spelling of ChatGPT found the product and the other found nothing —
        and no amount of character folding turns one transliteration into
        another. A trigram compares the shapes of the letters instead of the
        words, so a missing ی, a doubled letter or a swapped pair still
        matches. It runs only when the exact pass finds nothing: fuzzy results
        for a query that had a real answer would be noise, and a miss is the
        only moment where a near-match is better than an empty page.

        0.4 is measured, not guessed — see
        `test_the_fuzzy_threshold_separates_real_spellings_from_noise`."""
        needle = normalize_for_search(query)
        if not needle:
            return self.none()
        matches = self
        # Bounded: a 100-character query is already capped by the view, and no
        # real search needs more than a handful of words to be precise.
        for word in needle.split()[:8]:
            matches = matches.filter(search_text__icontains=word)
        if matches.exists():
            return matches
        return (
            self.annotate(closeness=TrigramWordSimilarity(needle, "search_text"))
            .filter(closeness__gte=FUZZY_THRESHOLD)
            .order_by("-closeness", "name")
        )


class Product(ChecksNameTheirField, models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("پیش‌نویس")
        ACTIVE = "active", _("فعال")
        UNAVAILABLE = "unavailable", _("ناموجود")

    class DeliveryType(models.TextChoices):
        READY_ACCOUNT = "ready_account", _("اکانت آماده")
        ON_CUSTOMER_ACCOUNT = "on_customer_account", _("فعال‌سازی روی اکانت شخصی")
        CODE_LICENSE = "code_license", _("کد لایسنس")
        GIFT_CARD = "gift_card", _("کد گیفت کارت")

    class Warranty(models.TextChoices):
        NONE = "none", _("بدون گارانتی")
        DAYS_7 = "days_7", _("۷ روز")
        FULL_PERIOD = "full_period", _("تا پایان دوره")

    CHECK_FIELDS: ClassVar[dict[str, str]] = {
        "product_delivery_hours_1_to_72": "delivery_hours",
        "product_status_valid": "status",
    }

    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products", verbose_name=_("دسته‌بندی")
    )
    name = models.CharField(_("نام"), max_length=150)
    # Optional on the form: a Latin name gives its own (see clean()). Unicode
    # is allowed only for the slugs saved before slugs were Latin, as on
    # Category.slug.
    slug = models.SlugField(
        _("نامک"),
        max_length=150,
        unique=True,
        blank=True,
        allow_unicode=True,
        help_text=_(
            "در نشانی صفحه می‌آید و لاتین است، مثل «chatgpt-plus». "
            "خالی بماند تا از نام لاتین ساخته شود."
        ),
    )
    short_description = models.CharField(
        _("توضیح کوتاه"),
        max_length=300,
        blank=True,
        default="",
    )
    full_description = models.TextField(
        _("توضیح کامل"),
        blank=True,
        default="",
    )
    image = models.ImageField(
        _("تصویر"),
        upload_to="products/",
        null=True,
        blank=True,
    )
    delivery_type = models.CharField(
        _("نوع تحویل"),
        max_length=24,
        choices=DeliveryType.choices,
    )
    # Search-only, never rendered: the spellings a customer actually types. A
    # Persian shopper writes «چت جیپت» for ChatGPT, and no amount of character
    # folding turns one transliteration into another — only this list does.
    aliases = models.TextField(
        _("نام‌های دیگر"),
        blank=True,
        default="",
        help_text=_("شکل‌هایی که مشتری ممکن است بنویسد، با ویرگول: چت جی‌پی‌تی، چت جیپت، gpt"),
    )
    warranty = models.CharField(
        _("گارانتی"),
        max_length=24,
        choices=Warranty.choices,
        default=Warranty.NONE,
    )
    delivery_hours = models.SmallIntegerField(
        _("مهلت تحویل (ساعت)"),
        default=24,
        help_text=_("بین ۱ تا ۷۲ ساعت. دست‌بالا بنویسید."),
    )
    delivery_note = models.CharField(
        _("متن دلخواه مهلت"),
        max_length=120,
        blank=True,
        default="",
        help_text=_(
            "اگر پر کنید، مشتری این متن را به‌جای «تا … ساعت» می‌بیند. "
            "عبارت کوتاه بنویسید، نه جمله."
        ),
    )
    # Claimed, never assumed. Renewal depends on the upstream provider and on
    # the operator being able to get the same account back, so an untouched
    # product says nothing rather than promising something it cannot keep —
    # and the page prints this only when it is true. The FAQ answers «تمدید
    # چطوریه؟» with "when the product says so", which is only honest while the
    # default is off.
    renewable = models.BooleanField(
        _("قابل تمدید"),
        default=False,
        help_text=_("روی صفحه‌ی محصول می‌نویسیم که تمدید می‌شود."),
    )
    delivery_template = models.TextField(
        _("قالب تحویل"),
        blank=True,
        default="",
        help_text=_("یادداشت خودتان. فعلاً جایی فرستاده نمی‌شود."),
    )
    status = models.CharField(
        _("وضعیت"),
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text=_("پیش‌نویس و ناموجود روی سایت دیده نمی‌شوند."),
    )
    # Maintained on save, never edited by hand. See search.py for what it folds.
    search_text = models.TextField(editable=False, blank=True, default="")
    seo_title = models.CharField(
        _("عنوان سئو"),
        max_length=70,
        blank=True,
        default="",
    )
    seo_description = models.CharField(
        _("توضیح سئو"),
        max_length=160,
        blank=True,
        default="",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[ProductQuerySet] = ProductQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        verbose_name = _("محصول")
        verbose_name_plural = _("محصولات")
        ordering = ["name"]
        # Serves the trigram fallback in `search()`. It does nothing at a
        # dozen rows and everything at a thousand; declared here so the model
        # and the migration state agree about it existing.
        indexes = [
            GinIndex(
                fields=["search_text"],
                opclasses=["gin_trgm_ops"],
                name="product_search_text_trgm",
            )
        ]
        constraints = [
            # The cap is the delivery promise on every product page: zero would
            # read as instant, and anything past the cap breaks a promise the
            # shop prints. Raised from 48 to 72 on the owner's ruling,
            # 2026-10-18 (ADR-0009, amended) — the FAQ quotes this number to
            # customers, so the two move together or the page starts lying.
            models.CheckConstraint(
                condition=Q(delivery_hours__gte=1) & Q(delivery_hours__lte=72),
                name="product_delivery_hours_1_to_72",
                violation_error_message=_("مهلت تحویل باید بین ۱ تا ۷۲ ساعت باشد."),
            ),
            # `choices=` binds only the admin form; the database binds everything.
            models.CheckConstraint(
                condition=Q(status__in=["draft", "active", "unavailable"]),
                name="product_status_valid",
                violation_error_message=_("وضعیت محصول معتبر نیست."),
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slug_for_save(self.name)
        self.search_text = normalize_for_search(
            f"{self.name} {self.short_description} {self.aliases}"
        )
        super().save(*args, **kwargs)

    def clean(self) -> None:
        if not self.slug:
            self.slug = latin_slug(self.name)
            if not self.slug:
                raise ValidationError(
                    {
                        "slug": _(
                            "نام محصول لاتین نیست، پس نامک را خودتان بنویسید، مثل «chatgpt-plus»."
                        )
                    }
                )
        check_slug_is_latin(self)

    @property
    def delivery_display(self) -> str:
        """The one phrase the shop uses to say when a product arrives.

        `delivery_hours` stays a number because it is the operator's real
        deadline — the panel counts down from it, and a CHECK keeps it inside
        the hours every page promises. But a number cannot say "slower this
        week because the gateway is flaky", so an optional note replaces the
        *display* and nothing else.
        """
        if self.delivery_note:
            return self.delivery_note
        return f"تا {to_persian_digits(self.delivery_hours)} ساعت"

    @property
    def delivery_sentence(self) -> str:
        """The same fact where it stands alone, on a badge or under the buy box.

        The panel asks for the note as a phrase in place of «تا … ساعت», so it
        takes the same word: «تحویل ۴۸ تا ۷۲ ساعت». Bare, it said nothing about
        what the hours were.
        """
        return f"تحویل {self.delivery_display}"

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
    """One fact about one product, written by the operator.

    This is deliberately free text rather than a column per fact. Whether an
    account is shared and with how many people, how many devices it allows,
    which region it is locked to, any condition particular to one product —
    none of these is true of every product, and a column that is blank on
    most rows is a column the form asks about anyway. The same reasoning
    removed `Product.region` in 0003.

    `is_key` is what makes that workable: a row marked key is printed beside
    the title where a buyer scans, instead of only in the table further down.
    So the operator decides both the fact and its weight.
    """

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="specs", verbose_name=_("محصول")
    )
    title = models.CharField(_("عنوان"), max_length=100)
    value = models.CharField(_("مقدار"), max_length=255)
    is_key = models.BooleanField(
        _("مهم"),
        default=False,
        help_text=_("کنار نام محصول، بالای صفحه‌ی محصول می‌آید."),
    )
    sort_order = models.SmallIntegerField(
        _("ترتیب نمایش"), default=0, help_text=_("عدد کوچک‌تر، بالاتر.")
    )

    class Meta:
        verbose_name = _("مشخصه")
        verbose_name_plural = _("مشخصات")
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.title}: {self.value}"


class Plan(ChecksNameTheirField, models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="plans", verbose_name=_("محصول")
    )
    title = models.CharField(_("عنوان"), max_length=100)
    duration_days = models.IntegerField(
        _("مدت (روز)"),
        null=True,
        blank=True,
        help_text=_("برای گیفت کارت خالی بگذارید."),
    )  # null: never expires
    cost_price = MoneyField(
        verbose_name=_("قیمت خرید"),
        help_text=_("مشتری این را نمی‌بیند."),
    )  # operator-only; snapshotted at order time
    sale_price = MoneyField(verbose_name=_("قیمت فروش"))
    promo_price = MoneyField(
        verbose_name=_("قیمت تخفیفی"),
        null=True,
        blank=True,
        help_text=_("باید از قیمت فروش کمتر باشد."),
    )
    promo_starts_at = models.DateTimeField(
        _("شروع تخفیف"), null=True, blank=True, help_text=_("خالی یعنی همین حالا.")
    )
    promo_ends_at = models.DateTimeField(
        _("پایان تخفیف"),
        null=True,
        blank=True,
        help_text=_(
            "با تعیین تاریخ، روی سایت شمارش معکوس نمایش داده می‌شود. خالی یعنی بدون پایان."
        ),
    )
    is_available = models.BooleanField(
        _("موجود"),
        default=True,
        help_text=_("تیک را بردارید تا خریدنی نباشد. پاکش نکنید."),
    )  # soft delete: sold plans are never removed
    # Default 1, so a quantity control appears only where the operator has
    # said it makes sense. Most of this shop is subscriptions activated on one
    # person's account, where "how many" is not a question the buyer has — and
    # a stepper on every product invites an order nobody can deliver. Gift
    # cards are the case that wants it.
    max_quantity = models.PositiveSmallIntegerField(
        _("حداکثر تعداد در هر سفارش"),
        default=1,
        help_text=_("۱ یعنی مشتری تعداد نمی‌بیند."),
    )
    requires_customer_input = models.BooleanField(
        _("نیاز به اطلاعات مشتری"),
        default=False,
        help_text=_("برای فعال‌سازی چیزی از مشتری لازم است؟"),
    )
    # The page prints it inside «برای فعال‌سازی، … را می‌پرسیم.», so a whole
    # sentence here broke that sentence on the product page.
    customer_input_label = models.CharField(
        _("چه چیزی از مشتری بپرسیم؟"),
        max_length=200,
        blank=True,
        default="",
        help_text=_(
            "عبارت کوتاه، نه جمله. روی سایت می‌آید: برای فعال‌سازی، … را می‌پرسیم. "
            "مثل: ایمیل اکانت Spotify"
        ),
    )
    supplier_url = models.URLField(
        _("لینک تأمین‌کننده"),
        blank=True,
        default="",
        help_text=_("مشتری این را نمی‌بیند."),
    )
    sort_order = models.SmallIntegerField(
        _("ترتیب نمایش"), default=0, help_text=_("عدد کوچک‌تر، بالاتر.")
    )

    # The field the operator would change to satisfy each CHECK.
    CHECK_FIELDS: ClassVar[dict[str, str]] = {
        "plan_promo_price_below_sale_price": "promo_price",
        "plan_promo_window_starts_before_it_ends": "promo_starts_at",
        "plan_prices_not_negative": "sale_price",
        "plan_duration_positive_or_unlimited": "duration_days",
        "plan_max_quantity_1_to_10": "max_quantity",
        "plan_customer_input_is_one_per_order": "max_quantity",
    }

    class Meta:
        verbose_name = _("پلن")
        verbose_name_plural = _("پلن‌ها")
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
                violation_error_message=_(
                    "قیمت تخفیفی باید بیشتر از صفر و کمتر از قیمت فروش باشد."
                ),
            ),
            models.CheckConstraint(
                condition=Q(promo_starts_at__isnull=True)
                | Q(promo_ends_at__isnull=True)
                | Q(promo_starts_at__lt=F("promo_ends_at")),
                name="plan_promo_window_starts_before_it_ends",
                violation_error_message=_("شروع تخفیف باید قبل از پایان آن باشد."),
            ),
            # Zero stays allowed — data-model §3 says `>= 0`, and whether a
            # zero-priced plan may be sold is a checkout question for S4b.
            models.CheckConstraint(
                condition=Q(cost_price__gte=0) & Q(sale_price__gte=0),
                name="plan_prices_not_negative",
                violation_error_message=_("قیمت خرید و قیمت فروش نمی‌توانند منفی باشند."),
            ),
            # NULL means the plan never expires; zero or less would date the expiry
            # on or before delivery, and the renewal reminder would fire at once.
            models.CheckConstraint(
                condition=Q(duration_days__isnull=True) | Q(duration_days__gt=0),
                name="plan_duration_positive_or_unlimited",
                violation_error_message=_(
                    "مدت پلن باید دست‌کم یک روز باشد، یا خالی برای بی‌پایان."
                ),
            ),
            models.CheckConstraint(
                condition=Q(max_quantity__gte=1) & Q(max_quantity__lte=10),
                name="plan_max_quantity_1_to_10",
                violation_error_message=_("حداکثر تعداد باید بین ۱ تا ۱۰ باشد."),
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
                violation_error_message=_(
                    "پلنی که به اطلاعات مشتری نیاز دارد، در هر سفارش فقط یکی قابل خرید است."
                ),
            ),
        ]

    def __str__(self) -> str:
        # «۱ ماهه، Claude Pro». The name is isolated: unisolated, a Latin name
        # beside Persian is reordered by the bidi algorithm, and the em dash
        # that used to stand between them read «۱ — Claude Pro ماهه».
        return f"{self.title}، \u2068{self.product}\u2069"

    @property
    def effective_price(self) -> Decimal:
        from apps.catalog.pricing import effective_price

        return effective_price(self)

    @property
    def promotion_is_active(self) -> bool:
        return self.effective_price != self.sale_price

    @property
    def promo_seconds_left(self) -> int | None:
        """Seconds until this plan's discount ends, or None when nothing ends.

        The page sends this rather than the deadline itself: a countdown run
        against an absolute server time is wrong by however much the visitor's
        own clock is wrong, and plenty of them are. Same reason the login
        code's clock takes seconds-remaining.
        """
        if not self.promotion_is_active or self.promo_ends_at is None:
            return None
        return max(0, int((self.promo_ends_at - timezone.now()).total_seconds()))

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

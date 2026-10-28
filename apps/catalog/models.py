"""The catalog: what is on the shelf, at what price, and what its page says.

Flat tables. A Category is a shelf; a Product is a thing on it; a Plan is one
way to buy that thing («۱ ماهه», «۳ ماهه») with its own price; a ProductSpec is
one row of the product's fact table. The page's prose is ProductSection rows,
each of a fixed kind with a template of its own, and its questions are
ProductQuestion rows. No category tree and no JSON specs — both were considered
and cut (ADR-0014): a few dozen products do not need a hierarchy, and a spec you
cannot query or edit in admin is not worth storing.

Money is toman, whole units, as a DecimalField with no decimal places: there is
no fractional toman, and Decimal keeps every arithmetic exact all the way to the
order snapshot. The same definition is used by every money column in the project.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Collection
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any, ClassVar, cast

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import TrigramWordSimilarity
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.files.base import ContentFile
from django.core.validators import RegexValidator, URLValidator, validate_slug
from django.db import models, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import Truncator, slugify
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps

from apps.catalog.search import normalize_for_search
from apps.core.formatting import to_persian_digits

# How close a misspelling has to be before the shop offers it as the answer.
# Measured against every seeded product: real misspellings of a product name
# score 0.5 and up, and the highest any unrelated word reaches is well under
# this. Raise it if a search starts answering the wrong question.
FUZZY_THRESHOLD = 0.4

# A Persian or Arabic letter. A name with one has no Latin slug to give.
_PERSIAN = re.compile(r"[\u0600-\u06ff]")

# What the content writers leave where only the owner's decision or the trial
# delivery can fill in the text. A page that still holds one is not published.
PLACEHOLDER = re.compile(r"\[(?:تصمیم مالک|وابسته به تصمیم|از تحویل آزمایشی)[^\]\n]*\]?")

# What the product page tells the buyer never to type into this site
# (catalog/_activation_answer.html), so no plan may ask for it. Matched on the
# label as normalize_for_search folds it, so «كد‌تأیید» arrives as «کد تایید».
# Persian words match inside a word, Latin ones only whole: «otp» is in «SpotPlayer».
ASKS_FOR_A_SECRET = re.compile(
    r"رمز|پا?سورد|گذر ?واژه|کد ورود|کد تا[یئ]ید|کد یک ?بار ?مصرف|\b(?:passwords?|otp)\b"
)

# A time-sensitive product's claims must have been checked this recently to publish.
FACTS_FRESH_DAYS = 7

# The stance of a product the Iran question never arises for. It claims nothing
# the provider says, so it needs no source and the page prints nothing about Iran.
NOT_APPLICABLE = normalize_for_search("موضوعیت ندارد")

# The fields whose change re-dates a plan's price confirmation.
PRICE_FIELDS = ("cost_price", "sale_price", "promo_price")

# Uploads are redrawn by Pillow before they are stored, so the file on disk is a
# new encoding with no metadata, and the original never reaches MEDIA_ROOT,
# which the proxy serves as it is. SVG is not on the list: it can carry script.
IMAGE_SUFFIXES = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# Decoding is what costs memory, at four bytes a pixel: 25 megapixels is a
# phone photo with room to spare, and 100 MB of RAM at most.
MAX_UPLOAD_PIXELS = 25_000_000


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


def check_seo_title(row: Category | Product) -> None:
    """base.html adds « | PremShop» to every title, so the field never holds it."""
    if "premshop" in row.seo_title.casefold():
        raise ValidationError(
            {"seo_title": _("نام فروشگاه را ننویسید. سایت خودش آن را به آخر عنوان اضافه می‌کند.")}
        )


def check_no_typed_discount(row: Product | Plan, *fields: str) -> None:
    """A discount is worked out from the plan's own prices and printed beside them.
    Typed into the text it outlives the offer and can contradict that figure."""
    refused = {
        field: _(
            "تخفیف را اینجا ننویسید. "
            "سایت درصد آن را از قیمت تخفیفی پلن حساب می‌کند و کنار قیمت نشان می‌دهد."
        )
        for field in fields
        if any(mark in normalize_for_search(getattr(row, field)) for mark in ("٪", "%", "تخفیف"))
    }
    if refused:
        raise ValidationError(refused)


def validate_image_upload(file: Any) -> None:
    """PNG, JPEG or WebP, judged by what Pillow reads rather than by the name.

    A file already stored is left alone. Every seeded logo is an SVG, and this
    runs on each save of the product form: refusing it would lock the form.
    """
    if getattr(file, "_committed", True):
        return
    if file.size > MAX_UPLOAD_BYTES:
        raise ValidationError(_("حجم تصویر بیشتر از ۵ مگابایت است."))
    try:
        with Image.open(file) as image:
            kind, (width, height) = image.format, image.size
            if kind in IMAGE_SUFFIXES and width * height <= MAX_UPLOAD_PIXELS:
                # The header alone passes a file cut off in transit, which
                # then failed in pre_save as a server error.
                image.load()
    except OSError, SyntaxError, Image.DecompressionBombError:
        kind, width, height = None, 0, 0
    finally:
        file.seek(0)
    if kind not in IMAGE_SUFFIXES:
        raise ValidationError(_("این فایل PNG،\u200f JPEG یا WebP نیست، یا خراب است."))
    if width * height > MAX_UPLOAD_PIXELS:
        raise ValidationError(_("ابعاد تصویر بیشتر از ۲۵ مگاپیکسل است."))


def reencoded(file: Any, max_side: int) -> tuple[ContentFile[bytes], str]:
    """The upload redrawn to fit `max_side`, in its own format, and a new name.

    Only the colour profile is carried over; EXIF (a photo's location), XMP,
    comments and text chunks stay behind with the original bytes. The name is
    random because the uploaded one is the operator's, not the shop's.
    """
    file.seek(0)
    with Image.open(file) as source:
        kind = source.format or ""
        profile = source.info.get("icc_profile")
        # Turned by its EXIF orientation first, or a phone photo lies on its side.
        image = ImageOps.exif_transpose(source)
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    see_through = image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info
    image = image.convert("RGBA" if see_through and kind != "JPEG" else "RGB")
    image.info = {}
    extra = {"icc_profile": profile} if profile else {}
    out = BytesIO()
    if kind == "PNG":
        image.save(out, "PNG", optimize=True, **extra)
    elif kind == "WEBP":
        image.save(out, "WEBP", quality=85, method=6, **extra)
    else:
        image.save(out, "JPEG", quality=85, optimize=True, progressive=True, **extra)
    return ContentFile(out.getvalue()), secrets.token_hex(8) + IMAGE_SUFFIXES[kind]


class ReencodedImageField(models.ImageField):
    """An ImageField whose upload is stored only as `reencoded` made it."""

    default_validators = [validate_image_upload]

    def __init__(self, *args: Any, max_side: int = 1600, **kwargs: Any) -> None:
        self.max_side = max_side
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> Any:
        name, path, args, kwargs = super().deconstruct()
        kwargs["max_side"] = self.max_side
        return name, path, args, kwargs

    def formfield(self, **kwargs: Any) -> Any:
        field: Any = super().formfield(**kwargs)
        field.widget.attrs["accept"] = "image/png,image/jpeg,image/webp"
        return field

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        file = getattr(model_instance, self.attname)
        if file and not file._committed:
            file.file, file.name = reencoded(file, self.max_side)
        return super().pre_save(model_instance, add)


def validate_source_lines(text: str) -> None:
    """One http(s) address per line; blank lines are allowed."""
    check = URLValidator(schemes=["http", "https"])
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                check(line.strip())
            except ValidationError:
                raise ValidationError(
                    _("سطر %(line)s نشانی کامل نیست، مثل https://openai.com/chatgpt/pricing"),
                    params={"line": to_persian_digits(number)},
                ) from None


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
    # Kept, out of the form, until the restricted-Markdown `intro` that 0020
    # converted it into has been live a while: reversing 0020 needs it intact.
    intro_html = models.TextField(
        _("متن معرفی"),
        blank=True,
        default="",
        help_text=_("برچسب HTML اجرا نمی‌شود و همان‌طور که نوشته شده دیده می‌شود."),
    )
    # Markdown through apps.core.richtext with no heading level free: the
    # introduction sits directly under the page's h1.
    intro = models.TextField(
        _("متن معرفی"),
        blank=True,
        default="",
        help_text=_(
            "Markdown ساده: پاراگراف، **پررنگ**، پیوند و فهرست. تیتر به متن ساده تبدیل می‌شود."
        ),
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
        check_seo_title(self)


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
        "product_delivery_hours_1_to_48": "delivery_hours",
        "product_status_valid": "status",
    }
    # What a visitor reads of this row, and so where a placeholder must not be.
    PAGE_TEXT: ClassVar[tuple[str, ...]] = (
        "name",
        "short_description",
        "usage_notes",
        "refund_note",
        "delivery_note",
        "seo_title",
        "seo_description",
    )

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
    # Plain text, never Markdown: with no seo_description it is the meta description.
    short_description = models.CharField(
        _("جمع‌بندی یک‌خطی"),
        max_length=300,
        blank=True,
        default="",
        help_text=_("یک جمله: محصول برای خریدار چه می‌کند و به کار چه کسی می‌آید."),
    )
    # Off the form and off the page: 0020 moved it into a «متن آزاد» section.
    # The column stays until that migration no longer needs reversing.
    full_description = models.TextField(
        _("توضیح کامل"),
        blank=True,
        default="",
    )
    image = ReencodedImageField(
        _("تصویر"),
        upload_to="products/",
        null=True,
        blank=True,
        max_side=512,
        help_text=_(
            "لوگوی محصول، PNG،\u200f JPEG یا WebP. مربع، با حاشیه‌ی شفاف. سایت آن را در اندازه‌ی ثابت نشان می‌دهد."
        ),
    )
    brand_colour = models.CharField(
        _("رنگ برند"),
        max_length=7,
        blank=True,
        default="",
        validators=[RegexValidator(r"\A#[0-9a-fA-F]{6}\Z", _("رنگ را مانند #10a37f وارد کنید."))],
        help_text=_("رنگ زمینه‌ی کارت، مانند #10a37f. خالی یعنی زمینه‌ی خنثی."),
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
        help_text=_("بین ۱ تا ۴۸ ساعت. دست‌بالا بنویسید."),
    )
    delivery_note = models.CharField(
        _("متن دلخواه مهلت"),
        max_length=120,
        blank=True,
        default="",
        help_text=_(
            "اگر پر کنید، مشتری همین متن را همان‌طور که نوشته‌اید "
            "به‌جای «تحویل تا … ساعت» می‌بیند."
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
    # Restricted Markdown with no heading level free: it is printed inside
    # «پیش از خرید», under that section's own h2. Required to publish, so no
    # product goes live without the operator having said what applies to it.
    usage_notes = models.TextField(
        _("نکات استفاده"),
        blank=True,
        default="",
        help_text=_("هر نکته در یک سطر که با - شروع می‌شود. اگر نکته‌ای ندارد، «موضوعیت ندارد»."),
    )
    # A fact the page prints from its own field. The sections never repeat it,
    # so two parts of one page cannot disagree.
    refund_note = models.CharField(
        _("استثنای بازگشت وجه"),
        max_length=300,
        blank=True,
        default="",
        help_text=_("فقط اگر این محصول با قاعده‌ی بازگشت وجه فروشگاه فرق دارد. یک جمله."),
    )
    sources = models.TextField(
        _("منبع‌ها"),
        blank=True,
        default="",
        validators=[validate_source_lines],
        help_text=_(
            "نشانی صفحه‌های رسمی پشتوانه‌ی ادعاهای متن، هر کدام در یک سطر. روی سایت نمی‌آید."
        ),
    )
    facts_checked_on = models.DateField(
        _("آخرین بررسی"),
        null=True,
        blank=True,
        help_text=_("روزی که ادعاهای متن را دوباره با منبع‌ها سنجیدید."),
    )
    time_sensitive = models.BooleanField(
        _("حساس به زمان"),
        default=False,
        help_text=_(
            "امکانات یا قیمت ارائه‌دهنده زود عوض می‌شود. "
            "انتشار، بررسی در ۷ روز اخیر را لازم دارد."
        ),
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
            # Keep the database limit and the shared delivery promise together.
            models.CheckConstraint(
                condition=Q(delivery_hours__gte=1) & Q(delivery_hours__lte=48),
                name="product_delivery_hours_1_to_48",
                violation_error_message=_("مهلت تحویل باید بین ۱ تا ۴۸ ساعت باشد."),
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

    def get_absolute_url(self) -> str:
        return reverse("product", args=[self.slug])

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
        check_seo_title(self)
        check_no_typed_discount(self, "name", "seo_title", "seo_description")
        if self.facts_checked_on and self.facts_checked_on > timezone.localdate():
            raise ValidationError({"facts_checked_on": _("روز بررسی نمی‌تواند بعد از امروز باشد.")})
        # The product form's half of the publishing rule: this row's own fields.
        # The rows under it are checked by the admin's inline formsets, because
        # Django validates this form before those. A product already live is
        # held to the placeholders only, so a fact that went stale on it does
        # not lock every other edit.
        if self.status == self.Status.ACTIVE:
            refused: dict[str, list[str]] = {}
            live = self.is_live_in_db()
            if not live:
                for field, message in self.fact_problems().items():
                    refused.setdefault(field, []).append(message)
            for field, message in placeholders_in(self):
                refused.setdefault(field, []).append(message)
            if refused:
                why = (
                    gettext("محصول منتشرشده با جای خالی پرنشده ذخیره نمی‌شود.")
                    if live
                    else gettext("تا کمبودهای این فرم رفع نشود، منتشر نمی‌شود.")
                )
                refused.setdefault("status", []).append(why)
                raise ValidationError(refused)

    def is_live_in_db(self) -> bool:
        """Whether the stored row is published, whatever this instance now says."""
        return (
            self.pk is not None
            and Product.objects.filter(pk=self.pk, status=self.Status.ACTIVE).exists()
        )

    @property
    def usage_notes_shown(self) -> str:
        """The notes as the page prints them: nothing where none apply."""
        if normalize_for_search(self.usage_notes).rstrip(".") == NOT_APPLICABLE:
            return ""
        return self.usage_notes.strip()

    @property
    def facts_are_fresh(self) -> bool:
        cutoff = timezone.localdate() - timedelta(days=FACTS_FRESH_DAYS)
        return self.facts_checked_on is not None and self.facts_checked_on >= cutoff

    def fact_problems(self) -> dict[str, str]:
        """What publishing needs from this row's own fields, by the field to fix."""
        problems: dict[str, str] = {}
        if self.delivery_type not in self.DeliveryType.values:
            problems["delivery_type"] = gettext("نوع تحویل انتخاب نشده است.")
        if not self.short_description.strip():
            problems["short_description"] = gettext("جمع‌بندی یک‌خطی خالی است.")
        if not self.usage_notes.strip():
            problems["usage_notes"] = gettext(
                "نکات استفاده نوشته نشده است. اگر نکته‌ای ندارد، بنویسید «موضوعیت ندارد»."
            )
        if self.time_sensitive and not self.facts_are_fresh:
            problems["facts_checked_on"] = gettext(
                "این محصول حساس به زمان است و ادعاهایش در ۷ روز اخیر بررسی نشده‌اند."
            )
        return problems

    def publish_problems(self) -> list[str]:
        """Every reason this product may not go live, in Persian; empty when it may.

        The rows under it are read as stored, which is what the bulk action and
        the readiness list on the product form need. The form itself asks the
        same questions of what the operator submitted: `clean()` above for this
        row, the admin's inline formsets for the rows under it.
        """
        problems = list(self.fact_problems().values())
        if not any(plan.is_available for plan in self.plans.all()):
            problems.append(str(NO_AVAILABLE_PLAN))
        rows = [
            self,
            *self.specs.all(),
            *self.plans.all(),
            *self.sections.all(),
            *self.questions.all(),
        ]
        problems += [message for row in rows for _field, message in placeholders_in(row)]
        return problems

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

        A note is the operator's own sentence, so nothing can be prefixed to it:
        «تحویل» in front of «به‌علت مشکلات پرداخت…» reads as a typo. The default
        carries its own word instead, and the note is printed exactly as written.
        """
        return self.delivery_note or f"تحویل تا {to_persian_digits(self.delivery_hours)} ساعت"

    @property
    def is_public(self) -> bool:
        return self.status == self.Status.ACTIVE

    def available_plans(self) -> list[Plan]:
        """Iterates plans.all() so a prefetch is honoured — a card grid of thirty
        products must not become thirty queries."""
        return [plan for plan in self.plans.all() if plan.is_in_stock]

    @property
    def cheapest_plan(self) -> Plan | None:
        plans = self.available_plans()
        return min(plans, key=lambda plan: plan.effective_price) if plans else None

    @property
    def card_colour(self) -> str:
        return (
            self.brand_colour if re.fullmatch(r"#[0-9a-fA-F]{6}", self.brand_colour) else "#737373"
        )

    @property
    def discounted_plans(self) -> list[Plan]:
        return sorted(
            (plan for plan in self.available_plans() if plan.promotion_is_active),
            key=lambda plan: plan.discount_percent,
            reverse=True,
        )


NO_AVAILABLE_PLAN = _("هیچ پلن موجودی ندارد.")


def placeholders_in(row: models.Model) -> list[tuple[str, str]]:
    """(field, message) for each field in `row.PAGE_TEXT` that still holds a placeholder.

    The message names the row and the field, and quotes the placeholder, so it
    reads the same under the field on the form and in a list for the bulk action.
    """
    where = ""
    if not isinstance(row, Product):
        # Isolated, like Plan.__str__: a Latin title would otherwise pull the
        # Persian around it out of order.
        names = {"kind": row._meta.verbose_name, "row": f"\u2068{row}\u2069"}
        where = gettext("%(kind)s «%(row)s»، ") % names
    found = []
    for name in row.PAGE_TEXT:  # type: ignore[attr-defined]
        if match := PLACEHOLDER.search(getattr(row, name)):
            message = gettext("%(where)s%(field)s: جای خالی «%(marker)s» هنوز پر نشده است.") % {
                "where": where,
                "field": cast(models.Field, row._meta.get_field(name)).verbose_name,
                "marker": Truncator(match.group(0)).chars(60),
            }
            found.append((name, message))
    return found


class PageRow(models.Model):
    """A row its product's page prints: on a live product it holds no placeholder.

    Asked in clean(), so every form that saves a row asks it, the plan's own
    form in the panel included. On the product form a row's product is the
    form's instance, carrying the status as submitted.
    """

    class Meta:
        abstract = True

    def clean(self) -> None:
        super().clean()
        product = getattr(self, "product", None)
        if product is None or product.status != Product.Status.ACTIVE:
            return
        refused: dict[str, list[str]] = {}
        for field, message in placeholders_in(self):
            refused.setdefault(field, []).append(message)
        if refused:
            raise ValidationError(refused)


class ProductSpec(PageRow):
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

    PAGE_TEXT: ClassVar[tuple[str, ...]] = ("title", "value")

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


class Plan(ChecksNameTheirField, PageRow):
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
    stock_quantity = models.PositiveIntegerField(
        _("موجودی کل"),
        null=True,
        blank=True,
        help_text=_(
            "تعداد قابل فروش. افزودن به سبد خرید موجودی را رزرو نمی‌کند. خالی یعنی بدون محدودیت موجودی."
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
    customer_input_label = models.CharField(
        _("چه چیزی از مشتری بپرسیم؟"),
        max_length=200,
        blank=True,
        default="",
        help_text=_("عبارت کوتاه؛ مثل: ایمیل اکانت Spotify."),
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
    # When someone last stood behind this price: set by any change to a price
    # and by the panel's «قیمت‌ها را امروز بررسی کردم» for its review filters.
    price_confirmed_at = models.DateTimeField(
        _("تأیید قیمت"), null=True, blank=True, editable=False
    )

    PAGE_TEXT: ClassVar[tuple[str, ...]] = ("title", "customer_input_label")
    # The field the operator would change to satisfy each CHECK.
    CHECK_FIELDS: ClassVar[dict[str, str]] = {
        "plan_promo_price_below_sale_price": "promo_price",
        "plan_promo_window_starts_before_it_ends": "promo_starts_at",
        "plan_prices_not_negative": "sale_price",
        "plan_duration_positive_or_unlimited": "duration_days",
        "plan_max_quantity_1_to_10": "max_quantity",
        "plan_customer_input_is_one_per_order": "max_quantity",
        "plan_stock_not_negative": "stock_quantity",
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
            models.CheckConstraint(
                condition=Q(stock_quantity__isnull=True) | Q(stock_quantity__gte=0),
                name="plan_stock_not_negative",
                violation_error_message=_("موجودی نمی‌تواند منفی باشد."),
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

    @transaction.atomic
    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk and self.stock_quantity is not None:
            Plan.objects.select_for_update().get(pk=self.pk)
        # A price someone has just typed is a price someone has just checked.
        fields = kwargs.get("update_fields")
        if (fields is None or set(fields) & set(PRICE_FIELDS)) and self._price_is_new():
            self.price_confirmed_at = timezone.now()
            if fields is not None:
                kwargs["update_fields"] = {*fields, "price_confirmed_at"}
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        check_no_typed_discount(self, "title")
        # Whatever the product's status: a draft's label is what goes live.
        if ASKS_FOR_A_SECRET.search(normalize_for_search(self.customer_input_label)):
            raise ValidationError(
                {
                    "customer_input_label": _(
                        "رمز اکانت یا کدی را که سرویس می‌فرستد از مشتری نپرسید."
                        " صفحه‌ی محصول از مشتری می‌خواهد این‌ها را در سایت وارد نکند."
                    )
                }
            )

    def _price_is_new(self) -> bool:
        if self._state.adding:
            return True
        stored = Plan.objects.filter(pk=self.pk).values(*PRICE_FIELDS).first()
        return stored is None or any(stored[name] != getattr(self, name) for name in PRICE_FIELDS)

    @property
    def is_in_stock(self) -> bool:
        return self.is_available and (self.stock_quantity is None or self.stock_quantity > 0)

    @property
    def effective_price(self) -> Decimal:
        from apps.catalog.pricing import effective_price

        return effective_price(self)

    @property
    def promotion_is_active(self) -> bool:
        return self.effective_price != self.sale_price

    @property
    def discount_percent(self) -> int:
        return (
            int((self.sale_price - self.effective_price) * 100 / self.sale_price)
            if self.sale_price
            else 0
        )

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


class ProductSection(ChecksNameTheirField, PageRow):
    """One part of a product page's prose, of a fixed kind.

    The kind decides the template and the default title, so the operator
    chooses which parts a product needs and in what order, never how one
    looks. Facts with fields of their own (price, delivery, warranty, refunds,
    the provider's stance on Iran) are not written here: «پیش از خرید» prints
    them from those fields, even when its own text is empty.
    """

    class Kind(models.TextChoices):
        BENEFITS = "benefits", _("مزیت‌ها و کاربردها")
        INCLUDES = "includes", _("چه چیزهایی می‌گیرید")
        COMPARISON = "comparison", _("مقایسه")
        AFTER_PAYMENT = "after_payment", _("بعد از پرداخت")
        BEFORE_BUYING = "before_buying", _("پیش از خرید")
        FREE = "free", _("متن آزاد")

    PAGE_TEXT: ClassVar[tuple[str, ...]] = ("title", "body", "image_alt")
    CHECK_FIELDS: ClassVar[dict[str, str]] = {
        "section_kind_valid": "kind",
        "section_one_before_buying": "kind",
        "section_image_has_alt": "image_alt",
    }

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="sections", verbose_name=_("محصول")
    )
    kind = models.CharField(
        _("نوع"),
        max_length=16,
        choices=Kind.choices,
        help_text=_("قالب بخش را تعیین می‌کند. هر محصول حداکثر یک «پیش از خرید» دارد."),
    )
    title = models.CharField(
        _("تیتر"),
        max_length=120,
        blank=True,
        default="",
        help_text=_("خالی بماند تا نام همین نوع تیتر شود. «متن آزاد» بی‌تیتر می‌ماند."),
    )
    # Restricted Markdown, through apps.core.richtext with h3 free: the
    # section's own title is the h2.
    body = models.TextField(
        _("متن"),
        blank=True,
        default="",
        help_text=_("Markdown ساده: **پررنگ**، فهرست با - یا ۱.، پیوند، جدول و تیتر ###."),
    )
    image = ReencodedImageField(
        _("تصویر"),
        upload_to="sections/",
        blank=True,
        max_side=1600,
        help_text=_(
            "PNG،\u200f JPEG یا WebP. هیچ‌وقت اطلاعات ورود اکانت یا داده‌ی مشتری را نشان ندهد."
        ),
    )
    image_alt = models.CharField(
        _("متن جایگزین تصویر"),
        max_length=200,
        blank=True,
        default="",
        help_text=_("آنچه تصویر نشان می‌دهد، برای کسی که آن را نمی‌بیند. با تصویر لازم است."),
    )
    sort_order = models.SmallIntegerField(
        _("ترتیب نمایش"), default=0, help_text=_("عدد کوچک‌تر، بالاتر.")
    )

    class Meta:
        verbose_name = _("بخش محتوا")
        verbose_name_plural = _("بخش‌های محتوا")
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    kind__in=[
                        "benefits",
                        "includes",
                        "comparison",
                        "after_payment",
                        "before_buying",
                        "free",
                    ]
                ),
                name="section_kind_valid",
                violation_error_message=_("نوع بخش معتبر نیست."),
            ),
            # The conditions of a sale are said once, in one place on the page.
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(kind="before_buying"),
                name="section_one_before_buying",
                violation_error_message=_("این محصول بخش «پیش از خرید» دارد. همان را ویرایش کنید."),
            ),
            models.CheckConstraint(
                condition=Q(image="") | ~Q(image_alt=""),
                name="section_image_has_alt",
                violation_error_message=_("تصویر بدون متن جایگزین ذخیره نمی‌شود."),
            ),
        ]

    def __str__(self) -> str:
        return self.title or self.get_kind_display()


class ProductQuestion(PageRow):
    """A question a buyer asks about this product, in the buyer's own words.

    What «پیش از خرید» already answers (warranty, refunds, a restricted
    account, the stance on Iran) is not asked again here.
    """

    PAGE_TEXT: ClassVar[tuple[str, ...]] = ("question", "answer")

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="questions", verbose_name=_("محصول")
    )
    question = models.CharField(
        _("پرسش"),
        max_length=200,
        help_text=_("با صدای خریدار، همان‌طور که در تلگرام می‌پرسد."),
    )
    # Restricted Markdown, like a section's text.
    answer = models.TextField(
        _("پاسخ"),
        help_text=_("اصل جواب در جمله‌ی اول، معمولاً یکی دو جمله."),
    )
    sort_order = models.SmallIntegerField(
        _("ترتیب نمایش"), default=0, help_text=_("عدد کوچک‌تر، بالاتر.")
    )

    class Meta:
        verbose_name = _("پرسش")
        verbose_name_plural = _("پرسش‌های محصول")
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.question

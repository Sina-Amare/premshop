from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.content import PLACEHOLDER


class Page(models.Model):
    class Slug(models.TextChoices):
        TERMS = "terms", _("قوانین خرید")
        REFUNDS = "refunds", _("لغو سفارش و بازگشت وجه")
        PRIVACY = "privacy", _("حریم خصوصی")
        ABOUT = "about", _("درباره ما")
        CONTACT = "contact", _("تماس با ما")

    slug = models.SlugField(_("آدرس صفحه"), max_length=20, unique=True, choices=Slug.choices)
    title = models.CharField(_("عنوان"), max_length=160)
    body = models.TextField(
        _("متن صفحه"),
        blank=True,
        help_text=_("متن با مارک‌داون: ## برای تیتر، - برای فهرست و [عنوان](آدرس) برای لینک."),
    )
    description = models.CharField(
        _("توضیح کوتاه"),
        max_length=300,
        blank=True,
        help_text=_("توضیح صفحه برای نتیجه‌ی جستجو و پیش‌نمایش لینک."),
    )
    is_published = models.BooleanField(
        _("منتشرشده"),
        default=False,
        help_text=_("انتشار پس از تأیید متن و آماده‌شدن فرایندهای اعلام‌شده در آن."),
    )
    updated_at = models.DateTimeField(_("آخرین ویرایش"), auto_now=True)

    class Meta:
        verbose_name = _("صفحه")
        verbose_name_plural = _("صفحه‌ها")
        ordering = ("pk",)
        permissions = [("publish_page", "انتشار صفحه")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(slug__in=["terms", "refunds", "privacy", "about", "contact"]),
                name="cms_page_slug_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(is_published=False)
                | (~models.Q(title="") & ~models.Q(body="") & ~models.Q(description="")),
                name="cms_published_page_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse(f"cms:{self.slug}")

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(slug=self.slug).exists():
            errors["slug"] = _("آدرس صفحه پس از افزودن تغییر نمی‌کند.")
        if self.is_published:
            for name in ("title", "body", "description"):
                value = getattr(self, name)
                if not value.strip():
                    errors[name] = _("برای انتشار، این قسمت را کامل کنید.")
                elif PLACEHOLDER.search(value):
                    errors[name] = _("پیش از انتشار، جای خالی تصمیم یا اطلاعات را پر کنید.")
        if errors:
            raise ValidationError(errors)

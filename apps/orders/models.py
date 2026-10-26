"""Purchase facts, unpaid items and their permanent history."""

from __future__ import annotations

import secrets
from typing import Any

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import connection, models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import MoneyField
from apps.core.fields import EncryptedTextField
from apps.core.formatting import to_persian_digits


def next_order_number() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('orders_order_number_seq')")
        return int(cursor.fetchone()[0])


def tracking_token() -> str:
    return secrets.token_urlsafe(16)


class DiscountCode(models.Model):
    class Kind(models.TextChoices):
        PERCENT = "percent", _("درصدی")
        FIXED = "fixed", _("مبلغ ثابت")

    class Scope(models.TextChoices):
        ALL = "all", _("همهٔ محصولات")
        SELECTED = "selected", _("محصولات انتخاب‌شده")

    code = models.CharField(
        _("کد"), max_length=8, unique=True, validators=[RegexValidator(r"\A[A-Z0-9]{4,8}\Z")]
    )
    kind = models.CharField(_("نوع"), max_length=8, choices=Kind.choices)
    value = MoneyField(verbose_name=_("مقدار"))
    scope = models.CharField(_("دامنه"), max_length=8, choices=Scope.choices, default=Scope.ALL)
    products = models.ManyToManyField("catalog.Product", blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    per_user_limit = models.PositiveIntegerField(null=True, blank=True)
    min_order_amount = MoneyField(null=True, blank=True)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("کد تخفیف")
        verbose_name_plural = _("کدهای تخفیف")
        constraints = [
            models.CheckConstraint(
                condition=Q(code__regex=r"^[A-Z0-9]{4,8}$"), name="discount_code_format"
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["percent", "fixed"]), name="discount_kind_valid"
            ),
            models.CheckConstraint(
                condition=Q(scope__in=["all", "selected"]), name="discount_scope_valid"
            ),
            models.CheckConstraint(condition=Q(value__gt=0), name="discount_value_positive"),
            models.CheckConstraint(
                condition=Q(max_uses__isnull=True) | Q(max_uses__gt=0),
                name="discount_max_uses_positive",
            ),
            models.CheckConstraint(
                condition=Q(per_user_limit__isnull=True) | Q(per_user_limit__gt=0),
                name="discount_user_limit_positive",
            ),
            models.CheckConstraint(
                condition=Q(min_order_amount__isnull=True) | Q(min_order_amount__gte=0),
                name="discount_minimum_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(valid_from__isnull=True)
                | Q(valid_until__isnull=True)
                | Q(valid_from__lt=F("valid_until")),
                name="discount_window_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)


class Order(models.Model):
    class Channel(models.TextChoices):
        WEB = "web", _("سایت")
        BOT = "bot", _("ربات")
        LEGACY = "legacy", _("سفارش قبلی")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name=_("مشتری"),
    )
    order_number = models.BigIntegerField(
        _("شمارهٔ سفارش"), default=next_order_number, unique=True, editable=False
    )
    tracking_token = models.CharField(
        max_length=32, default=tracking_token, unique=True, editable=False
    )
    subtotal = MoneyField(verbose_name=_("جمع اقلام"))
    discount_code = models.ForeignKey(DiscountCode, on_delete=models.PROTECT, null=True, blank=True)
    discount_amount = MoneyField(verbose_name=_("تخفیف"), default=0)
    total_amount = MoneyField(verbose_name=_("جمع نهایی"))
    channel = models.CharField(
        _("مسیر ثبت"), max_length=8, choices=Channel.choices, default=Channel.WEB
    )
    created_at = models.DateTimeField(_("زمان ثبت"), auto_now_add=True)

    class Meta:
        verbose_name = _("سفارش")
        verbose_name_plural = _("سفارش‌ها")
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(subtotal__gte=0) & Q(discount_amount__gte=0) & Q(total_amount__gte=0),
                name="order_amounts_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(total_amount=F("subtotal") - F("discount_amount")),
                name="order_total_matches_ledger",
            ),
            models.CheckConstraint(
                condition=Q(channel__in=["web", "bot", "legacy"]), name="order_channel_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"سفارش {to_persian_digits(self.order_number)}"


class OrderItem(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "PENDING_PAYMENT", _("منتظر پرداخت")
        QUEUED = "QUEUED", _("در صف انجام")
        AWAITING_INPUT = "AWAITING_INPUT", _("منتظر اطلاعات مشتری")
        DELIVERED = "DELIVERED", _("تحویل داده شده")
        REPLACEMENT_REQUESTED = "REPLACEMENT_REQUESTED", _("درخواست تعویض")
        CANCELLED = "CANCELLED", _("لغو شده")
        REFUNDED = "REFUNDED", _("بازپرداخت شده")

    class CancelReason(models.TextChoices):
        EXPIRED_UNPAID = "expired_unpaid", _("مهلت پرداخت تمام شده")
        CUSTOMER_BEFORE_PAYMENT = "customer_before_payment", _("درخواست مشتری پیش از پرداخت")
        CUSTOMER_AFTER_PAYMENT = "customer_after_payment", _("درخواست مشتری پس از پرداخت")
        SUPPLY_FAILURE = "supply_failure", _("امکان تأمین نیست")
        INPUT_TIMEOUT = "input_timeout", _("اطلاعات مشتری دریافت نشده")
        WARRANTY_REFUND = "warranty_refund", _("بازپرداخت گارانتی")
        OPERATOR = "operator", _("پشتیبانی لغو کرد")

    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name="items", verbose_name=_("سفارش")
    )
    plan = models.ForeignKey("catalog.Plan", on_delete=models.PROTECT, verbose_name=_("پلن"))
    status = models.CharField(
        _("وضعیت"), max_length=24, choices=Status.choices, default=Status.PENDING_PAYMENT
    )
    price_snapshot = MoneyField(verbose_name=_("قیمت فروش زمان خرید"))
    cost_snapshot = MoneyField(verbose_name=_("قیمت تأمین زمان خرید"))
    product_snapshot = models.JSONField(_("اطلاعات زمان خرید"))
    customer_input = EncryptedTextField(blank=True, default="")
    cancel_reason = models.CharField(  # noqa: DJ001 - no reason until cancellation
        _("دلیل لغو"), max_length=32, choices=CancelReason.choices, null=True, blank=True
    )
    created_at = models.DateTimeField(_("زمان ثبت"), auto_now_add=True)

    class Meta:
        verbose_name = _("آیتم سفارش")
        verbose_name_plural = _("آیتم‌های سفارش")
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "PENDING_PAYMENT",
                        "QUEUED",
                        "AWAITING_INPUT",
                        "DELIVERED",
                        "REPLACEMENT_REQUESTED",
                        "CANCELLED",
                        "REFUNDED",
                    ]
                ),
                name="order_item_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(price_snapshot__gte=0) & Q(cost_snapshot__gte=0),
                name="order_item_prices_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(cancel_reason__isnull=True)
                | Q(
                    cancel_reason__in=[
                        "expired_unpaid",
                        "customer_before_payment",
                        "customer_after_payment",
                        "supply_failure",
                        "input_timeout",
                        "warranty_refund",
                        "operator",
                    ]
                ),
                name="order_item_cancel_reason_valid",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=["CANCELLED", "REFUNDED"]) | Q(cancel_reason__isnull=False),
                name="order_item_cancel_has_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order} · {self.product_snapshot.get('name', '')}"


class OrderItemEvent(models.Model):
    class Actor(models.TextChoices):
        OPERATOR = "operator", _("پشتیبانی")
        CUSTOMER = "customer", _("مشتری")
        SYSTEM = "system", _("سیستم")

    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="events")
    from_status = models.CharField(  # noqa: DJ001 - creation has no previous state
        max_length=24, choices=OrderItem.Status.choices, null=True
    )
    to_status = models.CharField(max_length=24, choices=OrderItem.Status.choices)
    actor = models.CharField(max_length=12, choices=Actor.choices)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(actor__in=["operator", "customer", "system"]),
                name="order_event_actor_valid",
            ),
            models.CheckConstraint(
                condition=Q(from_status__isnull=True) | Q(from_status__in=OrderItem.Status.values),
                name="order_event_from_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(to_status__in=OrderItem.Status.values),
                name="order_event_to_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{to_persian_digits(self.order_item_id)} · {self.get_to_status_display()}"


class DiscountRedemption(models.Model):
    discount_code = models.ForeignKey(DiscountCode, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    amount = MoneyField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0), name="discount_redemption_not_negative"
            )
        ]
        indexes = [models.Index(fields=["discount_code", "user"])]

    def __str__(self) -> str:
        return f"سفارش {to_persian_digits(self.order_id)}"

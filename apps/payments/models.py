from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import MoneyField


class Payment(models.Model):
    class Method(models.TextChoices):
        MANUAL = "manual", _("خارج از درگاه")
        GATEWAY = "gateway", _("درگاه")

    class Status(models.TextChoices):
        CREATED = "created", _("ثبت‌شده")
        INITIATED = "initiated", _("در حال پرداخت")
        VERIFIED = "verified", _("تأییدشده")
        FAILED = "failed", _("ناموفق")
        ABANDONED = "abandoned", _("تکمیل‌نشده")

    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="payments", verbose_name=_("سفارش")
    )
    method = models.CharField(_("روش پرداخت"), max_length=16, choices=Method.choices)
    status = models.CharField(
        _("وضعیت"), max_length=12, choices=Status.choices, default=Status.CREATED
    )
    amount = MoneyField(verbose_name=_("مبلغ دریافتی"))
    bank_ref = models.CharField(_("شماره‌ی پیگیری بانک"), max_length=64, blank=True, default="")
    matched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, verbose_name=_("ثبت‌کننده")
    )
    created_at = models.DateTimeField(_("زمان ثبت"), default=timezone.now, editable=False)
    verified_at = models.DateTimeField(_("زمان تأیید"), null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("پرداخت")
        verbose_name_plural = _("پرداخت‌ها")
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["order"],
                condition=Q(status="verified"),
                name="payment_one_verified_per_order",
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.CheckConstraint(
                condition=Q(method__in=["manual", "gateway"]), name="payment_method_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["created", "initiated", "verified", "failed", "abandoned"]),
                name="payment_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(method="manual") | (Q(matched_by__isnull=False) & ~Q(bank_ref="")),
                name="payment_manual_has_reference",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="verified", verified_at__isnull=False)
                    | (~Q(status="verified") & Q(verified_at__isnull=True))
                ),
                name="payment_verification_has_time",
            ),
        ]

    def __str__(self) -> str:
        return str(self.order)


class Refund(models.Model):
    """Money owed back for one cancelled paid item, then the transfer that returned it.

    Database triggers keep it: never deleted, an executed refund never changes, an owed one
    changes only by becoming executed, it never exceeds its item's paid amount nor the order's
    refunds its verified payment, and no item is REFUNDED without an executed one.
    """

    class Status(models.TextChoices):
        OWED = "owed", _("در انتظار واریز")
        EXECUTED = "executed", _("واریز شد")

    class Destination(models.TextChoices):
        CARD = "card", _("کارت")
        SHEBA = "sheba", _("شبا")

    order_item = models.OneToOneField(
        "orders.OrderItem",
        on_delete=models.PROTECT,
        related_name="refund",
        verbose_name=_("آیتم سفارش"),
    )
    amount = MoneyField(verbose_name=_("مبلغ بازپرداخت"))
    status = models.CharField(
        _("وضعیت"), max_length=10, choices=Status.choices, default=Status.OWED
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("ثبت‌کننده"),
    )
    created_at = models.DateTimeField(_("زمان ثبت"), default=timezone.now, editable=False)
    # Only the kind and the last four digits: the bank's own record proves where it went.
    destination_kind = models.CharField(
        _("نوع مقصد"), max_length=8, choices=Destination.choices, blank=True, default=""
    )
    destination_last4 = models.CharField(
        _("چهار رقم آخر مقصد"), max_length=4, blank=True, default=""
    )
    bank_ref = models.CharField(_("شماره‌ی پیگیری بانک"), max_length=64, blank=True, default="")
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("واریزکننده"),
    )
    executed_at = models.DateTimeField(_("زمان واریز"), null=True, blank=True)

    class Meta:
        verbose_name = _("بازپرداخت")
        verbose_name_plural = _("بازپرداخت‌ها")
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="refund_amount_positive"),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="owed",
                        destination_kind="",
                        destination_last4="",
                        bank_ref="",
                        executed_by__isnull=True,
                        executed_at__isnull=True,
                    )
                    | (
                        Q(
                            status="executed",
                            destination_kind__in=["card", "sheba"],
                            destination_last4__regex=r"^[0-9]{4}$",
                            executed_by__isnull=False,
                            executed_at__isnull=False,
                        )
                        & ~Q(bank_ref="")
                    )
                ),
                name="refund_execution_complete",
            ),
        ]

    def __str__(self) -> str:
        return str(self.order_item)

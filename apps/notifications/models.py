from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):
    class Template(models.TextChoices):
        PURCHASE = "purchase_confirmation", _("تأییدیه‌ی خرید")
        PAYMENT = "payment_confirmed", _("تأیید پرداخت")
        INPUT = "input_requested", _("درخواست اطلاعات")
        DELIVERY = "delivery_completed", _("تحویل محصول")
        REJECTION = "replacement_rejected", _("رد درخواست تعویض")
        DELAY = "supply_delayed", _("تغییر مهلت تحویل")
        CANCELLATION = "item_cancelled", _("لغو محصول")
        REFUND = "item_refunded", _("واریز بازپرداخت")

    class Status(models.TextChoices):
        PENDING = "pending", _("در انتظار")
        SENT = "sent", _("ارسال‌شده")
        FAILED = "failed", _("ناموفق")

    class Channel(models.TextChoices):
        EMAIL = "email", _("ایمیل")
        TELEGRAM = "telegram", _("تلگرام")

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, verbose_name=_("سفارش"))
    dedupe_key = models.CharField(max_length=100, unique=True, editable=False)
    template_name = models.CharField(
        _("نوع پیام"),
        max_length=32,
        choices=Template.choices,
        default=Template.PURCHASE,
        editable=False,
    )
    channel = models.CharField(
        _("مسیر ارسال"),
        max_length=8,
        choices=Channel.choices,
        default=Channel.EMAIL,
        editable=False,
    )
    recipient = models.EmailField(_("گیرنده"), editable=False)
    # The chat linked when the row was queued; the sender drops the row, never redirects
    # it, if the customer's link has changed since.
    chat_id = models.BigIntegerField(_("گفت‌وگوی تلگرام"), null=True, blank=True, editable=False)
    subject = models.TextField(_("موضوع"), editable=False)
    text_body = models.TextField(_("متن ایمیل"), editable=False)
    html_body = models.TextField(_("نسخه‌ی HTML"), editable=False)
    status = models.CharField(
        _("وضعیت"), max_length=8, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveSmallIntegerField(_("تعداد تلاش‌ها"), default=0)
    next_attempt_at = models.DateTimeField(
        _("تلاش بعدی"), default=timezone.now, null=True, blank=True
    )
    created_at = models.DateTimeField(_("زمان ثبت"), default=timezone.now, editable=False)
    last_attempt_at = models.DateTimeField(_("آخرین تلاش"), null=True, blank=True, editable=False)
    sent_at = models.DateTimeField(_("زمان ارسال"), null=True, blank=True, editable=False)
    last_error_class = models.CharField(_("نوع خطا"), max_length=200, blank=True, editable=False)
    smtp_code = models.PositiveSmallIntegerField(
        _("کد SMTP"), null=True, blank=True, editable=False
    )

    class Meta:
        verbose_name = _("پیام سفارش")
        verbose_name_plural = _("پیام‌های سفارش")
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    template_name__in=[
                        "purchase_confirmation",
                        "payment_confirmed",
                        "input_requested",
                        "delivery_completed",
                        "replacement_rejected",
                        "supply_delayed",
                        "item_cancelled",
                        "item_refunded",
                    ]
                ),
                name="notification_template_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["pending", "sent", "failed"]),
                name="notification_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(attempts__lte=6), name="notification_attempts_bounded"
            ),
            models.CheckConstraint(
                condition=Q(channel="email", chat_id__isnull=True)
                | Q(channel="telegram", chat_id__isnull=False),
                name="notification_channel_recipient",
            ),
        ]

    def __str__(self) -> str:
        return str(self.order)


class ConfirmationSweep(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    quota_wait_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name="confirmation_sweep_singleton")
        ]

    def __str__(self) -> str:
        return "Confirmation quota wait"

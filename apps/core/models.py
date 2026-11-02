from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class MailAttempt(models.Model):
    class Status(models.TextChoices):
        IN_FLIGHT = "in_flight", _("در حال ارسال")
        ACCEPTED = "accepted", _("سرویس ایمیل پذیرفت")
        REJECTED = "rejected", _("ارسال ناموفق")
        UNCERTAIN = "uncertain", _("نتیجه نامشخص")
        BLOCKED = "blocked", _("متوقف‌شده به‌خاطر سقف ارسال")

    template = models.CharField(
        _("نوع ایمیل"),
        max_length=64,
        choices=[
            ("otp_code", _("کد ورود")),
            ("password_reset", _("کد بازیابی رمز عبور")),
            ("signin_alert", _("خبر ورود با کد")),
            ("password_changed", _("خبر تغییر رمز عبور")),
            ("item_delivered", _("خبر تحویل سفارش")),
        ],
    )
    recipient_hash = models.CharField(_("اثر انگشت گیرنده"), max_length=64, db_index=True)
    created_at = models.DateTimeField(_("زمان درخواست"), default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(_("زمان ثبت نتیجه"), null=True)
    status = models.CharField(_("نتیجه"), max_length=12, choices=Status, default=Status.IN_FLIGHT)
    error_type = models.CharField(_("نوع خطا"), max_length=64, blank=True)
    smtp_code = models.PositiveSmallIntegerField(_("شماره پاسخ سرویس"), null=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = _("سابقه ارسال ایمیل")
        verbose_name_plural = _("سابقه ارسال ایمیل‌ها")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["in_flight", "accepted", "rejected", "uncertain", "blocked"]
                ),
                name="mail_attempt_known_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.template} · {self.pk}"

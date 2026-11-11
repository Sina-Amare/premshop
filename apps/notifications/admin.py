from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from apps.core.admin import detail_fields
from apps.core.formatting import format_jalali, to_latin_digits, to_persian_digits
from apps.notifications.models import ConfirmationSweep, Notification


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    search_fields = ("=order__order_number", "recipient")

    def get_search_results(
        self, request: HttpRequest, queryset: QuerySet[Notification], search_term: str
    ) -> tuple[QuerySet[Notification], bool]:
        return super().get_search_results(request, queryset, to_latin_digits(search_term))

    list_display = (
        "order",
        "template_name",
        "channel",
        "status_pill",
        "recipient",
        "attempts_shown",
        "next_send",
        "last_error_class",
        "smtp_shown",
    )
    list_filter = ("status", "channel", "template_name")
    fields = readonly_fields = detail_fields(
        Notification, {"id": _("شناسه"), "dedupe_key": _("کلید یکتای پیام")}
    )
    actions = None
    list_select_related = ("order",)

    # The five status tones, as a plain column: the panel's CSS hides every `status_label`
    # column on a phone.
    @admin.display(description=_("وضعیت"), ordering="status")
    def status_pill(self, obj: Notification) -> str:
        tones: dict[str, str] = {
            Notification.Status.PENDING: "progress",
            Notification.Status.SENT: "done",
            Notification.Status.FAILED: "problem",
        }
        tone = tones.get(obj.status, "closed")
        return format_html(
            '<span class="panel-pill" data-tone="{}">{}</span>', tone, obj.get_status_display()
        )

    # Counts and codes in Persian digits, as every other figure in the panel.
    @admin.display(
        description=Notification._meta.get_field("attempts").verbose_name, ordering="attempts"
    )
    def attempts_shown(self, obj: Notification) -> str:
        return to_persian_digits(obj.attempts)

    @admin.display(
        description=Notification._meta.get_field("smtp_code").verbose_name, ordering="smtp_code"
    )
    def smtp_shown(self, obj: Notification) -> str:
        return to_persian_digits(obj.smtp_code) if obj.smtp_code else "—"

    @admin.display(description=_("تلاش بعدی"))
    def next_send(self, obj: Notification) -> str:
        if obj.status != "pending":
            return "—"
        sweep = ConfirmationSweep.objects.filter(pk=1).first()
        due = obj.next_attempt_at
        if sweep and sweep.quota_wait_until and sweep.quota_wait_until > timezone.now():
            due = max(due, sweep.quota_wait_until) if due else sweep.quota_wait_until
        return (
            format_jalali(due) + "، " + to_persian_digits(timezone.localtime(due).strftime("%H:%M"))
            if due
            else "—"
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Notification | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Notification | None = None) -> bool:
        return False

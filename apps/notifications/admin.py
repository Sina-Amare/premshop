from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

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
        "status",
        "recipient",
        "attempts",
        "next_send",
        "last_error_class",
        "smtp_code",
    )
    list_filter = ("status", "template_name")
    readonly_fields = tuple(field.name for field in Notification._meta.fields)
    actions = None
    list_select_related = ("order",)

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

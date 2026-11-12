from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db.models import DateTimeField, Model, PositiveSmallIntegerField, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from apps.core.email_log import fingerprint
from apps.core.formatting import format_jalali, to_persian_digits
from apps.core.models import MailAttempt


def detail_fields(model: type[Model], labels: dict[str, Any]) -> tuple[Any, ...]:
    """Every field of a read-only page in the panel's own terms: each moment in the Jalali
    calendar with its time, each count and service code in Persian digits, and a Persian label
    where the model gives none. Ids and chat ids stay as stored: they are copied, not read."""
    shown: list[Any] = []
    for field in model._meta.fields:
        if isinstance(field, DateTimeField | PositiveSmallIntegerField) or field.name in labels:
            shown.append(_shown(field.attname, labels.get(field.name, field.verbose_name)))
        else:
            shown.append(field.name)
    return tuple(shown)


def _shown(name: str, label: Any) -> Callable[[Any], str]:
    def show(obj: Any) -> str:
        value = getattr(obj, name)
        if value is None or value == "":
            return "—"
        if hasattr(value, "tzinfo"):
            moment = timezone.localtime(value)
            return f"{format_jalali(moment)}، {to_persian_digits(moment.strftime('%H:%M'))}"
        return to_persian_digits(value) if isinstance(value, int) else str(value)

    show.__name__ = f"{name}_shown"
    return admin.display(description=label)(show)


@admin.register(MailAttempt)
class MailAttemptAdmin(ModelAdmin):
    list_display = ("requested_at", "status_pill", "template", "smtp_shown", "error_type")
    list_filter = ("status", "template")
    search_fields = ("recipient_hash",)
    fields = readonly_fields = detail_fields(MailAttempt, {"id": _("شناسه")})
    change_list_template = "admin/core/mailattempt/change_list.html"
    actions = None

    # The five status tones, as a plain column: the panel's CSS hides every `status_label`
    # column on a phone. An unclear result, a refusal or a cap is a problem to look at.
    @admin.display(description=_("نتیجه"), ordering="status")
    def status_pill(self, obj: MailAttempt) -> str:
        tones: dict[str, str] = {
            MailAttempt.Status.IN_FLIGHT: "progress",
            MailAttempt.Status.ACCEPTED: "done",
            MailAttempt.Status.REJECTED: "problem",
            MailAttempt.Status.UNCERTAIN: "problem",
            MailAttempt.Status.BLOCKED: "problem",
        }
        tone = tones.get(obj.status, "closed")
        return format_html(
            '<span class="panel-pill" data-tone="{}">{}</span>', tone, obj.get_status_display()
        )

    @admin.display(description=_("زمان درخواست"), ordering="created_at")
    def requested_at(self, obj: MailAttempt) -> str:
        return format_html(
            "{}<br><bdi>{}</bdi>",
            format_jalali(obj.created_at),
            to_persian_digits(timezone.localtime(obj.created_at).strftime("%H:%M:%S")),
        )

    @admin.display(description=_("شماره پاسخ سرویس"), ordering="smtp_code")
    def smtp_shown(self, obj: MailAttempt) -> str:
        return "—" if obj.smtp_code is None else to_persian_digits(obj.smtp_code)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: MailAttempt | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: MailAttempt | None = None) -> bool:
        return False

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        if request.method == "POST" and "recipient" in request.POST:
            if not self.has_view_permission(request):
                raise PermissionDenied
            address = request.POST["recipient"].strip().lower()
            if not address:
                return HttpResponseRedirect(request.path)
            try:
                validate_email(address)
            except ValidationError:
                messages.error(request, _("نشانی ایمیل را کامل وارد کنید."))
                return HttpResponseRedirect(request.path)
            return HttpResponseRedirect(request.path + "?" + urlencode({"q": fingerprint(address)}))
        return super().changelist_view(request, extra_context)

    def get_search_results(
        self, request: HttpRequest, queryset: QuerySet[MailAttempt], search_term: str
    ) -> tuple[QuerySet[MailAttempt], bool]:
        if search_term:
            queryset = queryset.filter(recipient_hash=search_term)
        return queryset, False

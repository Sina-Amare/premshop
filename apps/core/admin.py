from typing import Any
from urllib.parse import urlencode

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from apps.core.email_log import fingerprint
from apps.core.models import MailAttempt


@admin.register(MailAttempt)
class MailAttemptAdmin(ModelAdmin):
    list_display = ("created_at", "template", "status", "smtp_code", "error_type")
    list_filter = ("status", "template")
    search_fields = ("recipient_hash",)
    readonly_fields = tuple(field.name for field in MailAttempt._meta.fields)
    change_list_template = "admin/core/mailattempt/change_list.html"
    actions = None

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

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from apps.cms.models import Page
from apps.core.formatting import format_jalali


@admin.register(Page)
class PageAdmin(ModelAdmin):
    list_display = ("title", "is_published", "modified")
    list_filter = ("is_published",)
    search_fields = ("title", "body")
    fields = ("address", "title", "body", "description", "is_published", "modified")
    actions = None

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def get_readonly_fields(self, request: HttpRequest, obj: Page | None = None) -> tuple[str, ...]:
        fields: tuple[str, ...] = ("address", "modified")
        if not request.user.has_perm("cms.publish_page"):
            fields += ("is_published",)
        return fields

    def has_change_permission(self, request: HttpRequest, obj: Page | None = None) -> bool:
        if obj and obj.is_published and not request.user.has_perm("cms.publish_page"):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request: HttpRequest, obj: Page | None = None) -> bool:
        return False

    def view_on_site(self, obj: Page) -> str:
        return obj.get_absolute_url()

    @admin.display(description=_("آدرس صفحه"))
    def address(self, obj: Page) -> SafeString:
        url = obj.get_absolute_url()
        return format_html('<a href="{}"><bdi dir="ltr">{}</bdi></a>', url, url)

    @admin.display(description=_("آخرین ویرایش"), ordering="updated_at")
    def modified(self, obj: Page | None) -> str:
        return format_jalali(obj.updated_at) if obj else ""

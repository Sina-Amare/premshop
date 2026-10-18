"""Customers in the admin: look one up, see whether the email is verified, deactivate.

Read-mostly by design. Passwords are never shown or set here — an OTP-only
account stays OTP-only until the customer sets one — and the operator panel
proper (S7) is where account actions belong. This exists so the customer list is
not invisible until then.
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.models import Group
from django.http import HttpRequest
from unfold.admin import ModelAdmin

from apps.accounts.models import User
from apps.core.formatting import format_jalali


@admin.register(User)
class UserAdmin(ModelAdmin):
    list_display = ["email", "is_verified", "phone", "joined", "is_active", "is_staff"]
    list_filter = ["is_verified", "is_active", "is_staff"]
    search_fields = ["email", "phone"]
    ordering = ["-date_joined"]
    readonly_fields = ["email", "date_joined", "last_login", "is_verified"]
    fields = ["email", "is_verified", "phone", "is_active", "is_staff", "date_joined", "last_login"]

    @admin.display(description="تاریخ عضویت", ordering="date_joined")
    def joined(self, obj: User) -> str:
        """Jalali, like every date the shop shows its operator."""
        return format_jalali(obj.date_joined)

    def has_add_permission(self, request: HttpRequest, obj: User | None = None) -> bool:
        # Accounts are created by the customer's first code, never by hand:
        # a hand-made account has an unverified address nobody proved.
        return False


# Groups are Django's role system, and this project does not have roles: there
# is one operator, and ADR-0014 parks RBAC until a second person needs a
# different view. An empty permission model on the panel's front page is a
# door to nowhere, so it is unregistered rather than left to confuse.
admin.site.unregister(Group)

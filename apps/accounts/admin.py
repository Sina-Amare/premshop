"""Customers in the admin: look one up, see whether the email is verified, deactivate.

Read-mostly by design. Passwords are never shown or set here — an OTP-only
account stays OTP-only until the customer sets one — and the operator panel
proper (S7) is where account actions belong. This exists so the customer list is
not invisible until then.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from unfold.admin import ModelAdmin

from apps.accounts.models import User


@admin.register(User)
class UserAdmin(ModelAdmin):
    list_display = ["email", "is_verified", "phone", "date_joined", "is_active", "is_staff"]
    list_filter = ["is_verified", "is_active", "is_staff"]
    search_fields = ["email", "phone"]
    ordering = ["-date_joined"]
    readonly_fields = ["email", "date_joined", "last_login", "is_verified"]
    fields = ["email", "is_verified", "phone", "is_active", "is_staff", "date_joined", "last_login"]

    def has_add_permission(self, request: HttpRequest, obj: User | None = None) -> bool:
        # Accounts are created by the customer's first code, never by hand:
        # a hand-made account has an unverified address nobody proved.
        return False

"""Customers in the admin: look one up, see whether the email is verified, deactivate.

Read-mostly by design. Passwords are never shown or set here — an OTP-only
account stays OTP-only until the customer sets one — and the operator panel
proper (S7) is where account actions belong. This exists so the customer list is
not invisible until then.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.forms import AuthenticationForm

from apps.accounts.models import User
from apps.accounts.services import password_attempt_refusal
from apps.core import ratelimit
from apps.core.formatting import format_jalali, to_persian_digits


class PanelLoginForm(AuthenticationForm):
    """The panel's sign-in, on the storefront's budget.

    Unfold's form underneath, which is Django's admin form with Unfold's
    styling on the two inputs.

    The panel is where every price on the shop is changed, and its sign-in was
    the one without a limit. These are the shop's two budgets under the same
    keys, so an account has one budget wherever it is guessed at: ten tries per
    address in fifteen minutes and thirty per IP in an hour. The window opens
    with the first attempt and no later guess moves it (ratelimit.hit).
    """

    def clean(self) -> dict[str, Any]:
        username = self.cleaned_data.get("username")
        if username and self.cleaned_data.get("password"):
            if refusal := password_attempt_refusal(self.request, username):
                raise ValidationError(refusal, code="rate_limited")
        cleaned = super().clean()
        # Only a sign-in that passed. A post with no password skips the check
        # and returns cleanly too; clearing on it would undo the limit.
        if self.get_user() is not None:
            ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, username)
        return cleaned


admin.site.login_form = PanelLoginForm


@admin.register(User)
class UserAdmin(ModelAdmin):
    list_display = ["email", "is_verified", "phone", "joined", "is_active", "is_staff"]
    list_filter = ["is_verified", "is_active", "is_staff"]
    search_fields = ["email", "phone"]
    ordering = ["-date_joined"]
    readonly_fields = ["email", "joined", "last_seen", "is_verified"]
    fieldsets = (
        (_("حساب کاربری"), {"fields": ("email", "is_verified", "phone")}),
        (_("دسترسی"), {"fields": ("is_active", "is_staff")}),
        (_("زمان‌ها"), {"fields": ("joined", "last_seen")}),
    )

    @admin.display(description=_("تاریخ عضویت"), ordering="date_joined")
    def joined(self, obj: User) -> str:
        """Jalali, like every date the shop shows its operator. The page
        printed both dates as Django does, «۲۰ اکتبر ۲۰۲۶»."""
        return format_jalali(obj.date_joined)

    @admin.display(description=_("آخرین ورود"))
    def last_seen(self, obj: User) -> str:
        if obj.last_login is None:
            return "—"
        when = timezone.localtime(obj.last_login)
        return f"{format_jalali(when)}، {to_persian_digits(when.strftime('%H:%M'))}"

    def has_add_permission(self, request: HttpRequest, obj: User | None = None) -> bool:
        # Accounts are created by the customer's first code, never by hand:
        # a hand-made account has an unverified address nobody proved.
        return False


# Groups are Django's role system, and this project does not have roles: there
# is one operator, and ADR-0014 parks RBAC until a second person needs a
# different view. An empty permission model on the panel's front page is a
# door to nowhere, so it is unregistered rather than left to confuse.
admin.site.unregister(Group)

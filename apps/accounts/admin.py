"""Customers in the admin: look one up by number, correct a number, deactivate.

Read-mostly by design. Passwords are never shown or set here: a customer has
none, and staff change theirs on the panel's own password page. A customer's
number changes here, through support, because it is the account's way in.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.forms import AuthenticationForm

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.validators import normalize_mobile_phone
from apps.core import ratelimit
from apps.core.admin_site import PASSWORD_BACKEND
from apps.core.formatting import format_jalali, to_persian_digits


class PanelLoginForm(AuthenticationForm):
    """The panel's sign-in, on the storefront's budget.

    Unfold's form underneath, which is Django's admin form with Unfold's
    styling on the two inputs.

    The panel is where every price on the shop is changed, and its sign-in was
    the one without a limit. These are the shop's two budgets under the same
    keys, so an account has one budget wherever it is guessed at: ten tries per
    number in fifteen minutes and thirty per IP in an hour. The window opens
    with the first attempt and no later guess moves it (ratelimit.hit). Staff
    sign in by phone, the account's username.
    """

    def clean(self) -> dict[str, Any]:
        typed = self.cleaned_data.get("username", "")
        try:
            username = normalize_mobile_phone(typed) or typed
        except ValidationError:
            # Not a number: it matches nobody, and still costs one hash and one try.
            username = typed
        self.cleaned_data["username"] = username
        if username and (password := self.cleaned_data.get("password")):
            try:
                self.user_cache = services.authenticate_password(self.request, username, password)
            except services.RateLimited as error:
                raise ValidationError(error.message, code="rate_limited") from None
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            # The password backend, never the code's: the panel lets this session in.
            self.user_cache.backend = PASSWORD_BACKEND
            self.confirm_login_allowed(self.user_cache)
        # Only a sign-in that passed. A post with no password skips the check
        # and returns cleanly too; clearing on it would undo the limit.
        if self.get_user() is not None:
            ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_PHONE, username)
        return self.cleaned_data


admin.site.login_form = PanelLoginForm


class UserForm(forms.ModelForm):
    def clean_phone(self) -> str | None:
        # One normal form, as the database insists; blank is no number at all.
        return normalize_mobile_phone(self.cleaned_data.get("phone") or "") or None


@admin.register(User)
class UserAdmin(ModelAdmin):
    form = UserForm
    list_display = ["phone", "email", "is_verified", "joined", "is_active", "is_staff"]
    list_filter = ["is_verified", "is_active", "is_staff"]
    search_fields = ["phone", "email"]
    ordering = ["-date_joined"]
    readonly_fields = ["email", "joined", "last_seen", "is_verified"]
    fieldsets = (
        (_("حساب کاربری"), {"fields": ("phone", "is_verified", "email")}),
        (_("دسترسی"), {"fields": ("is_active", "is_staff")}),
        (_("تاریخ‌ها"), {"fields": ("joined", "last_seen")}),
    )

    def save_model(self, request: HttpRequest, obj: User, form: Any, change: bool) -> None:
        changed = set(form.changed_data) & {"phone", "is_active", "is_staff"}
        if "phone" in changed and not obj.has_usable_password():
            # Support moving a customer to a new number, after a lost SIM: a fresh
            # unusable mark changes the session hash, so whoever holds the old
            # number is signed out, and the new number is proven by its first code.
            obj.set_unusable_password()
            obj.is_verified = False
            changed |= {"password", "is_verified"}
        if changed:
            obj.save(update_fields=changed)

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

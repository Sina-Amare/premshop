"""Thin controllers: parse a form, call one service, render. No rules here."""

from __future__ import annotations

import secrets
from typing import cast

from django.contrib import messages
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts import ratelimit, services
from apps.accounts.forms import (
    CodeForm,
    EmailForm,
    PasswordLoginForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
)
from apps.accounts.models import User

# The address a code was sent to, carried between the two steps of the code flow.
# In the session rather than a hidden field so it cannot be swapped for someone
# else's address between requests.
PENDING_EMAIL_KEY = "accounts.pending_email"

WRONG_CREDENTIALS = "ایمیل یا رمز عبور درست نیست."
WRONG_CODE = "کد وارد شده درست نیست یا منقضی شده است."

# A real hash of a value nobody knows. Comparing a submitted password against it
# when the account does not exist makes the miss cost the same work as a genuine
# check; without it a nonexistent account answers measurably faster, which is a
# user-enumeration oracle. Built once at import, never per request.
_DUMMY_HASH = make_password(secrets.token_urlsafe(16))


@never_cache
@require_http_methods(["GET", "POST"])
def login_password(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("account")

    form = PasswordLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        ip = ratelimit.client_ip(request)
        if not ratelimit.hit(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email) or not ratelimit.hit(
            ratelimit.LOGIN_ATTEMPTS_PER_IP, ip
        ):
            form.add_error(None, services.TOO_MANY_ATTEMPTS)
        else:
            user = User.objects.filter(email__iexact=email).first()
            # check_password is run even when the user is missing, against a
            # throwaway hash, so a nonexistent account does not answer faster
            # than a wrong password. That timing difference is a user-enumeration
            # oracle, and the fix costs one hash.
            if (
                user is not None
                and user.is_active
                and user.check_password(form.cleaned_data["password"])
            ):
                django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email)
                return redirect("account")
            check_password(form.cleaned_data["password"], _DUMMY_HASH)
            form.add_error(None, WRONG_CREDENTIALS)

    return render(request, "accounts/login_password.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def login_code_request(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("account")

    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        try:
            services.request_login_code(request, email)
        except services.RateLimited as exc:
            form.add_error(None, exc.message)
        else:
            request.session[PENDING_EMAIL_KEY] = email
            return redirect("login-code-verify")

    return render(request, "accounts/login_code_request.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def login_code_verify(request: HttpRequest) -> HttpResponse:
    email = request.session.get(PENDING_EMAIL_KEY)
    if not email:
        return redirect("login-code")

    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = services.complete_code_login(request, email, form.cleaned_data["code"])
        except services.RateLimited as exc:
            form.add_error(None, exc.message)
        else:
            if user is not None:
                request.session.pop(PENDING_EMAIL_KEY, None)
                return redirect("account")
            form.add_error(None, WRONG_CODE)

    return render(request, "accounts/login_code_verify.html", {"form": form, "email": email})


@require_http_methods(["POST"])
def logout(request: HttpRequest) -> HttpResponse:
    """POST only. A GET logout can be triggered by any image tag on any site,
    which is a cross-site request forgery that logs your customers out for fun."""
    django_logout(request)
    return redirect("styleguide")


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def account(request: HttpRequest) -> HttpResponse:
    # An OTP-only account has no current password to ask for, and asking anyway
    # would be an unanswerable question on the page that is supposed to fix it.
    user = cast(User, request.user)  # @login_required guarantees this
    form_class = StyledPasswordChangeForm if user.has_usable_password() else StyledSetPasswordForm
    form = form_class(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        # Changing a password rotates the session hash, which would log the user
        # out of the tab they are looking at. Re-issue it so it does not.
        django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "رمز عبور تغییر کرد.")
        return redirect(reverse("account"))

    return render(request, "accounts/account.html", {"form": form})

"""Thin controllers: parse a form, call one service, render. No rules here."""

from __future__ import annotations

import secrets
import time
from typing import cast

from django.contrib import messages
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts import services
from apps.accounts.forms import (
    CodeForm,
    EmailForm,
    PasswordLoginForm,
    ProfileForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
)
from apps.accounts.models import User
from apps.core import ratelimit

# The address a code was sent to, carried between the two steps of the code flow.
# In the session rather than a hidden field so it cannot be swapped for someone
# else's address between requests.
PENDING_EMAIL_KEY = "accounts.pending_email"
PENDING_EXPIRES_KEY = "accounts.pending_expires_at"

# How long the resend button stays disabled after each send. Long enough that a
# customer does not fire twice while the first mail is still in flight, short
# enough that a mail that really did not arrive is not a two-minute wait.
RESEND_COOLDOWN_SECONDS = 60

WRONG_CREDENTIALS = "ایمیل یا رمز عبور اشتباه است."
WRONG_CODE = "کد اشتباه است یا منقضی شده."

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
            issued = services.request_login_code(request, email)
        except services.RateLimited as exc:
            form.add_error(None, exc.message)
        else:
            request.session[PENDING_EMAIL_KEY] = email
            request.session[PENDING_EXPIRES_KEY] = issued.expires_at
            return redirect("login-code-verify")

    return render(request, "accounts/login_code_request.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def login_code_verify(request: HttpRequest) -> HttpResponse:
    email = request.session.get(PENDING_EMAIL_KEY)
    if not email:
        return redirect("login-code")

    form = CodeForm(request.POST or None)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            user = services.complete_code_login(request, email, form.cleaned_data["code"])
        except services.RateLimited as exc:
            form.add_error(None, exc.message)
            status = 429
        else:
            if user is not None:
                request.session.pop(PENDING_EMAIL_KEY, None)
                request.session.pop(PENDING_EXPIRES_KEY, None)
                if _wants_json(request):
                    return JsonResponse({"ok": True, "next": reverse("account")})
                return redirect("account")
            form.add_error(None, WRONG_CODE)

    # The six-cell input submits by fetch so it can flash green before leaving the
    # page. Same view, same rules — only the shape of the answer changes, which is
    # why this is a header check and not a second endpoint.
    if request.method == "POST" and _wants_json(request):
        error = next(iter(form.non_field_errors()), None) or next(
            (e for errors in form.errors.values() for e in errors), WRONG_CODE
        )
        return JsonResponse({"ok": False, "error": str(error)}, status=status)

    return render(
        request,
        "accounts/login_code_verify.html",
        {
            "form": form,
            "email": email,
            "expires_in": _seconds_left(request.session.get(PENDING_EXPIRES_KEY, 0)),
            "resend_cooldown": RESEND_COOLDOWN_SECONDS,
        },
    )


def _wants_json(request: HttpRequest) -> bool:
    return "application/json" in request.headers.get("Accept", "")


def _seconds_left(expires_at: int) -> int:
    """Relative, not absolute: the browser counts down on its own clock from
    this number, so a wrong clock on the customer's machine cannot skew it."""
    return max(0, int(expires_at) - int(time.time()))


@never_cache
@require_http_methods(["POST"])
def login_code_resend(request: HttpRequest) -> HttpResponse:
    """Send a fresh code to the address already waiting in the session.

    Replaces the outstanding code (otp.issue always does) and hands back the new
    expiry so the page restarts its countdown. The same rate limit as the first
    request applies — three per address per fifteen minutes — so the button's
    60-second cooldown is a courtesy, not the defence.
    """
    email = request.session.get(PENDING_EMAIL_KEY)
    if not email:
        return JsonResponse({"ok": False, "error": "نشست شما منقضی شده است."}, status=400)
    try:
        issued = services.request_login_code(request, email)
    except services.RateLimited as exc:
        return JsonResponse({"ok": False, "error": exc.message}, status=429)
    request.session[PENDING_EXPIRES_KEY] = issued.expires_at
    return JsonResponse(
        {
            "ok": True,
            "expires_in": _seconds_left(issued.expires_at),
            "cooldown": RESEND_COOLDOWN_SECONDS,
        }
    )


@require_http_methods(["POST"])
def logout(request: HttpRequest) -> HttpResponse:
    """POST only. A GET logout can be triggered by any image tag on any site,
    which is a cross-site request forgery that logs your customers out for fun."""
    django_logout(request)
    return redirect("home")


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def account(request: HttpRequest) -> HttpResponse:
    # An OTP-only account has no current password to ask for, and asking anyway
    # would be an unanswerable question on the page that is supposed to fix it.
    user = cast(User, request.user)  # @login_required guarantees this
    password_form_class = (
        StyledPasswordChangeForm if user.has_usable_password() else StyledSetPasswordForm
    )
    # Two forms on one page, told apart by a hidden `form` field. Only the one
    # that was submitted is bound; the other renders clean, so a validation error
    # in one never paints the other red.
    which = request.POST.get("form") if request.method == "POST" else None
    password_form = password_form_class(user, request.POST if which == "password" else None)
    profile_form = ProfileForm(request.POST if which == "profile" else None, instance=user)

    if which == "password" and password_form.is_valid():
        user = password_form.save()
        # Changing a password rotates the session hash, which would log the user
        # out of the tab they are looking at. Re-issue it so it does not.
        django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "رمز عبور تغییر کرد.")
        return redirect(reverse("account"))

    if which == "profile" and profile_form.is_valid():
        profile_form.save()
        messages.success(request, "شماره تماس ذخیره شد.")
        return redirect(reverse("account"))

    return render(
        request,
        "accounts/account.html",
        {"form": password_form, "profile_form": profile_form},
    )

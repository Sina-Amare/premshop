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
from django.utils.http import url_has_allowed_host_and_scheme
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
from apps.core.sessions import login_session

# The address a code was sent to, carried between the two steps of the code flow.
# In the session rather than a hidden field so it cannot be swapped for someone
# else's address between requests.
PENDING_EMAIL_KEY = "accounts.pending_email"
PENDING_EXPIRES_KEY = "accounts.pending_expires_at"
PENDING_NEXT_KEY = "accounts.pending_next"

# How long the resend button stays disabled after each send. Long enough that a
# customer does not fire twice while the first mail is still in flight, short
# enough that a mail that really did not arrive is not a two-minute wait.
RESEND_COOLDOWN_SECONDS = 60

WRONG_CREDENTIALS = "ایمیل یا رمز عبور اشتباه است."
WRONG_CODE = "کد ورود درست نیست یا منقضی شده است."
CODE_RESENT = "کد جدید را فرستادیم."
# Shown on the code page by its script and on the email page without one, so
# it names the page that ran out.
PAGE_EXPIRED = "زمان صفحه‌ی کد ورود تمام شده است. دوباره ایمیلتان را وارد کنید."

# A real hash of a value nobody knows. Comparing a submitted password against it
# when there is no live account to check makes the miss cost the same work as a
# genuine check; without it a nonexistent account answers measurably faster, which
# is a user-enumeration oracle. Built once at import, never per request.
_DUMMY_HASH = make_password(secrets.token_urlsafe(16))


def _safe_next(request: HttpRequest) -> str:
    """Where to send someone once they are signed in.

    `@login_required` already puts the page they wanted in `?next=`, and until
    now both login views threw it away and sent everyone to `/account/`. That
    is only invisible while `/account/` is the one protected page; the moment
    the cart arrives, "sign in to see your order" would land on the wrong one.

    The host check is the whole security of this: without it `?next=` accepts
    an absolute URL and the login page becomes an open redirect, which is
    worth more to a phisher on a shop's own domain than on anyone else's.
    """
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return reverse("account")


@never_cache
@require_http_methods(["GET", "POST"])
def login_password(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(_safe_next(request))

    form = PasswordLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        if refusal := services.password_attempt_refusal(request, email):
            form.add_error(None, refusal)
        else:
            user = User.objects.filter(email__iexact=email).first()
            # Exactly ONE password hash per attempt, whatever the outcome. Any
            # difference in work is a difference in response time, and that tells a
            # stranger which addresses shop here. A live account costs its own check
            # (for an account with no password Django fakes that hash itself); every
            # other case costs one check against a throwaway hash. Never both — the
            # old code ran the throwaway after every failure, so a real account with
            # a wrong password cost two hashes and answered slower than a stranger.
            if user is not None and user.is_active:
                authenticated = user.check_password(form.cleaned_data["password"])
            else:
                check_password(form.cleaned_data["password"], _DUMMY_HASH)
                authenticated = False
            if authenticated:
                # Session-key rotation and login receivers must roll back together.
                with login_session(request):
                    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                ratelimit.reset(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, email)
                return redirect(_safe_next(request))
            form.add_error(None, WRONG_CREDENTIALS)

    # The destination rides a hidden field, because the form posts to this URL
    # without its query string.
    return render(
        request, "accounts/login_password.html", {"form": form, "next": _safe_next(request)}
    )


@never_cache
@require_http_methods(["GET", "POST"])
def login_code_request(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(_safe_next(request))

    # Back from «ایمیل اشتباه است؟» on the code page: the address the code went
    # to is already in the field, so a typo is corrected rather than retyped.
    form = EmailForm(
        request.POST or None, initial={"email": request.session.get(PENDING_EMAIL_KEY)}
    )
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        try:
            issued = services.request_login_code(request, email)
        except services.RateLimited as exc:
            form.add_error(None, exc.message)
        else:
            request.session[PENDING_EMAIL_KEY] = email
            request.session[PENDING_EXPIRES_KEY] = issued.expires_at
            # The code flow spans three requests, so the destination rides the
            # session with the address rather than the URL. It is validated
            # here, on the way in, and never re-read from the request after.
            request.session[PENDING_NEXT_KEY] = _safe_next(request)
            return redirect("login-code-verify")

    return render(
        request, "accounts/login_code_request.html", {"form": form, "next": _safe_next(request)}
    )


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
                destination = request.session.pop(PENDING_NEXT_KEY, None) or reverse("account")
                if _wants_json(request):
                    return JsonResponse({"ok": True, "next": destination})
                return redirect(destination)
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
            "next": request.session.get(PENDING_NEXT_KEY) or reverse("account"),
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

    Replaces the outstanding code (otp.issue always does). The page's script asks
    for JSON and restarts its countdown from the new expiry; a plain form post,
    from a browser with JavaScript off, is sent back to the page with a message.
    The same rate limit as the first request applies — three per address per
    fifteen minutes — so the button's 60-second cooldown is a courtesy, not the
    defence, and without JavaScript there is no cooldown at all.
    """
    email = request.session.get(PENDING_EMAIL_KEY)
    if not email:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": PAGE_EXPIRED}, status=400)
        messages.error(request, PAGE_EXPIRED)
        return redirect("login-code")
    try:
        issued = services.request_login_code(request, email)
    except services.RateLimited as exc:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": exc.message}, status=429)
        messages.error(request, exc.message)
        return redirect("login-code-verify")
    request.session[PENDING_EXPIRES_KEY] = issued.expires_at
    if not _wants_json(request):
        messages.success(request, CODE_RESENT)
        return redirect("login-code-verify")
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
def account(request: HttpRequest) -> HttpResponse:
    """Everything about the account itself, on one page.

    It used to be two: an overview that listed the phone number and a profile
    page that listed it again and let you edit it. Two pages, one fact, and a
    first word — «پیشخوان» — that said nothing about what was on them. The
    shop grows by adding sections that are genuinely different (سفارش‌ها at
    S4b, اشتراک‌ها at S9), not by splitting one.
    """
    user = cast(User, request.user)  # @login_required guarantees this
    form = ProfileForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        form.save()
        saved = "ذخیره شد" if user.phone else "حذف شد"
        messages.success(request, f"شماره‌ی موبایل {saved}.")
        return redirect(reverse("account"))
    return render(request, "accounts/account.html", {"profile_form": form, "section": "profile"})


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def account_password(request: HttpRequest) -> HttpResponse:
    # An OTP-only account has no current password to ask for, and asking anyway
    # would be an unanswerable question on the page that is supposed to fix it.
    user = cast(User, request.user)
    had_password = user.has_usable_password()
    form_class = StyledPasswordChangeForm if had_password else StyledSetPasswordForm
    form = form_class(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        # Changing a password rotates the session hash, which signs out every
        # other device and would sign out this tab too. Re-issue it here only.
        django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "رمز عبور تغییر کرد." if had_password else "رمز عبور ثبت شد.")
        return redirect(reverse("account-password"))
    return render(request, "accounts/account_password.html", {"form": form, "section": "password"})

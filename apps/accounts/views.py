"""Account forms and sign-in responses."""

from __future__ import annotations

import time
from typing import cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts import services, telegram
from apps.accounts.forms import ChannelForm, CodeForm, PhoneForm, ProfileForm
from apps.accounts.models import User
from apps.core import ratelimit
from apps.core.formatting import mask_phone
from apps.orders.selectors import current_order, customer_summary

# The number a code was sent to, carried between the two steps of the code flow.
# In the session rather than a hidden field so it cannot be swapped for someone
# else's number between requests.
PENDING_PHONE_KEY = "accounts.pending_phone"
PENDING_EXPIRES_KEY = "accounts.pending_expires_at"
PENDING_NEXT_KEY = "accounts.pending_next"
PENDING_CHANNEL_KEY = "accounts.pending_channel"
# The link token the account page is waiting on, kept server-side rather than in a URL.
TELEGRAM_LINK_KEY = "accounts.telegram_link"

# How long the resend button stays disabled after each send: the server takes one
# code per number every two minutes, so the button waits as long.
RESEND_COOLDOWN_SECONDS = 120

WRONG_CODE = "کد ورود درست نیست یا منقضی شده است."
CODE_RESENT = "کد جدید را فرستادیم."
# Shown on the code page by its script and on the number page without one, so
# it names the page that ran out.
PAGE_EXPIRED = "صفحه‌ی کد ورود دیگر کار نمی‌کند. دوباره شماره‌ی موبایلتان را وارد کنید."


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
def login_code_request(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if _wants_json(request):
            return JsonResponse({"ok": True, "next": _safe_next(request)})
        return redirect(_safe_next(request))

    # Back from «شماره اشتباه است؟» on the code page: the number the code went
    # to is already in the field, so a typo is corrected rather than retyped.
    form = PhoneForm(
        request.POST or None, initial={"phone": request.session.get(PENDING_PHONE_KEY)}
    )
    status = 400
    if request.method == "POST" and form.is_valid():
        phone = form.cleaned_data["phone"]
        try:
            issued = services.request_login_code(
                request, phone, channel=form.cleaned_data["channel"]
            )
        except services.CodeNotSent as exc:
            if _wants_json(request):
                return JsonResponse({"ok": False, "error": _as_text(exc)}, status=503)
            form.add_error(None, exc.message)
            status = 503
        except services.RateLimited as exc:
            form.add_error(None, exc.message)
            status = 429
        else:
            request.session[PENDING_PHONE_KEY] = phone
            request.session[PENDING_EXPIRES_KEY] = issued.expires_at
            request.session[PENDING_CHANNEL_KEY] = issued.channel
            # The code flow spans three requests, so the destination rides the
            # session with the number rather than the URL. It is validated
            # here, on the way in, and never re-read from the request after.
            request.session[PENDING_NEXT_KEY] = _safe_next(request)
            if _wants_json(request):
                return JsonResponse(
                    {
                        "ok": True,
                        "expires_in": _seconds_left(issued.expires_at),
                        "cooldown": RESEND_COOLDOWN_SECONDS,
                        "channel": issued.channel,
                    }
                )
            return redirect("login-code-verify")

    if request.method == "POST" and _wants_json(request):
        error = next((error for errors in form.errors.values() for error in errors), PAGE_EXPIRED)
        return JsonResponse({"ok": False, "error": str(error)}, status=status)
    return render(
        request,
        "accounts/login_code_request.html",
        {"form": form, "next": _safe_next(request)},
        status=503 if status == 503 else 200,
    )


@never_cache
@require_http_methods(["GET", "POST"])
def login_code_verify(request: HttpRequest) -> HttpResponse:
    phone = request.session.get(PENDING_PHONE_KEY)
    if not phone:
        if request.method == "POST" and _wants_json(request):
            return JsonResponse({"ok": False, "error": PAGE_EXPIRED}, status=400)
        return redirect("login-code")

    form = CodeForm(request.POST or None)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            user = services.complete_code_login(request, phone, form.cleaned_data["code"])
        except services.RateLimited as exc:
            form.add_error(None, exc.message)
            status = 429
        else:
            if user is not None:
                request.session.pop(PENDING_PHONE_KEY, None)
                request.session.pop(PENDING_EXPIRES_KEY, None)
                request.session.pop(PENDING_CHANNEL_KEY, None)
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
            "phone": mask_phone(phone),
            "expires_in": _seconds_left(request.session.get(PENDING_EXPIRES_KEY, 0)),
            "resend_cooldown": RESEND_COOLDOWN_SECONDS,
            "next": request.session.get(PENDING_NEXT_KEY) or reverse("account"),
            "channel": request.session.get(PENDING_CHANNEL_KEY, "sms"),
            "telegram_bot_url": f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}",
        },
    )


def _wants_json(request: HttpRequest) -> bool:
    return "application/json" in request.headers.get("Accept", "")


def _as_text(exc: services.CodeNotSent) -> str:
    """For a script, which sets the message as text, where markup would show."""
    return exc.text


def _seconds_left(expires_at: int) -> int:
    """Relative, not absolute: the browser counts down on its own clock from
    this number, so a wrong clock on the customer's machine cannot skew it."""
    return max(0, int(expires_at) - int(time.time()))


@never_cache
@require_http_methods(["POST"])
def login_code_resend(request: HttpRequest) -> HttpResponse:
    """Send a fresh code to the number already waiting in the session.

    Replaces the outstanding code once the new message is accepted; a send that
    fails leaves the code already on the phone, and its expiry, as they were. The
    page's script asks for JSON and restarts its countdown from the new expiry; a
    plain form post, from a browser with JavaScript off, is sent back to the page
    with a message.
    The same limits as the first request apply — one code per number every two
    minutes, five an hour — so the button's cooldown is a courtesy, not the
    defence, and without JavaScript there is no cooldown at all.
    """
    phone = request.session.get(PENDING_PHONE_KEY)
    if not phone:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": PAGE_EXPIRED}, status=400)
        messages.error(request, PAGE_EXPIRED)
        return redirect("login-code")
    channel_form = ChannelForm(
        {"channel": request.POST.get("channel", request.session.get(PENDING_CHANNEL_KEY, "sms"))}
    )
    if not channel_form.is_valid():
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": PAGE_EXPIRED}, status=400)
        messages.error(request, PAGE_EXPIRED)
        return redirect("login-code-verify")
    try:
        issued = services.request_login_code(
            request, phone, channel=channel_form.cleaned_data["channel"]
        )
    except services.CodeNotSent as exc:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": _as_text(exc)}, status=503)
        messages.error(request, exc.message)
        return redirect("login-code-verify")
    except services.RateLimited as exc:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": exc.message}, status=429)
        messages.error(request, exc.message)
        return redirect("login-code-verify")
    request.session[PENDING_EXPIRES_KEY] = issued.expires_at
    request.session[PENDING_CHANNEL_KEY] = issued.channel
    if not _wants_json(request):
        if issued.channel == "sms":
            messages.success(request, CODE_RESENT)
        return redirect("login-code-verify")
    return JsonResponse(
        {
            "ok": True,
            "expires_in": _seconds_left(issued.expires_at),
            "cooldown": RESEND_COOLDOWN_SECONDS,
            "channel": issued.channel,
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

    It used to be two: an overview that listed a fact and a profile page that
    listed it again and let you edit it. Two pages, one fact, and a first word —
    «پیشخوان» — that said nothing about what was on them. The shop grows by
    adding sections that are genuinely different, not by splitting one.
    """
    user = cast(User, request.user)  # @login_required guarantees this
    form = ProfileForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        form.save(commit=False).save(update_fields=["email"])
        saved = "ذخیره شد" if user.email else "حذف شد"
        messages.success(request, f"ایمیل {saved}.")
        return redirect(reverse("account"))
    current = current_order(user)
    return render(
        request,
        "accounts/account.html",
        {
            "profile_form": form,
            "section": "profile",
            "current_order": customer_summary(current) if current else None,
            "telegram": _telegram_section(request, user),
        },
    )


def _telegram_section(request: HttpRequest, user: User) -> dict[str, object] | None:
    if not settings.TELEGRAM_BOT_TOKEN:
        return None
    if user.telegram_id:
        request.session.pop(TELEGRAM_LINK_KEY, None)
        return {"linked": True, "username": user.telegram_username}
    token = request.session.get(TELEGRAM_LINK_KEY)
    if token and telegram.link_owner(token) != user:
        # Spent, expired or replaced: the page offers a fresh link instead.
        request.session.pop(TELEGRAM_LINK_KEY, None)
        token = None
    return {
        "linked": False,
        "token": token,
        "deep_link": telegram.deep_link(token) if token else "",
        "bot": settings.TELEGRAM_BOT_USERNAME,
    }


@never_cache
@login_required
@require_GET
def account_telegram_state(request: HttpRequest) -> JsonResponse:
    """What the waiting page asks until the bot's confirm goes through or the link expires."""
    section = _telegram_section(request, cast(User, request.user)) or {}
    if section.get("linked"):
        return JsonResponse({"state": "linked"})
    return JsonResponse({"state": "waiting" if section.get("token") else "unlinked"})


@never_cache
@login_required
@require_POST
def account_telegram(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    action = request.POST.get("action")
    # Unlink is never limited: it is what the link alert tells a customer to do, and only a
    # linked account can send its email, each link already bounded below and in the bot.
    if action == "link" and not ratelimit.hit(ratelimit.TELEGRAM_CHANGES_PER_ACCOUNT, str(user.pk)):
        messages.error(request, "سقف تغییر اتصال تلگرام برای امروز پر شده است.")
    elif action == "link" and settings.TELEGRAM_BOT_TOKEN:
        request.session[TELEGRAM_LINK_KEY] = telegram.start_link(user)
    elif action == "unlink":
        telegram.unlink(user)
        request.session.pop(TELEGRAM_LINK_KEY, None)
        messages.success(request, "اتصال تلگرام قطع شد.")
    return redirect(reverse("account") + "#telegram")

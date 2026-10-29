"""Honest pages instead of tracebacks, and the panel's language."""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from redis.exceptions import RedisError

from apps.core.observability import redact_url

logger = logging.getLogger(__name__)


class OrderPagePrivacy:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        tracking = request.path.lower().startswith("/track/")
        if tracking and response.status_code >= 400:
            allowed = response.get("Allow")
            response = render(
                None,
                "orders/tracking_error.html",
                {
                    "unavailable": response.status_code >= 500,
                    "limited": response.status_code == 429,
                },
                status=response.status_code,
            )
            if allowed:
                response["Allow"] = allowed
            if response.status_code == 429:
                response["Retry-After"] = "60"
        if tracking or request.path.startswith("/account/orders/"):
            patch_cache_control(
                response,
                private=True,
                no_store=True,
                no_cache=True,
                max_age=0,
                must_revalidate=True,
            )
            response["X-Robots-Tag"] = "noindex, nofollow"
            if tracking:
                response["Referrer-Policy"] = "no-referrer"
            response["X-Frame-Options"] = "DENY"
            response["X-Content-Type-Options"] = "nosniff"
        return response


class RejectNul:
    """An address with a NUL in it is no page here: a 404 before anything queries.

    PostgreSQL refuses a NUL inside a string, so a slug or a search query
    carrying one reached the database and came back as a 500, on any URL. A
    cookie with one is dropped instead, before the session lookup reads it, and
    the visitor carries on without it.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        query = request.META.get("QUERY_STRING", "")
        if "\x00" in request.path or "\x00" in query or "%00" in query:
            raise Http404
        for name in [name for name, value in request.COOKIES.items() if "\x00" in value]:
            del request.COOKIES[name]
        return self.get_response(request)


class CacheUnavailableMiddleware:
    """Render a Persian 503 when Redis is unreachable, anywhere in the app.

    Login codes and every rate-limit counter live in the cache, so a Redis outage
    genuinely breaks authentication — there is no failing open here, because
    failing open would mean unlimited guesses at a six-digit code. What is NOT
    acceptable is what happened before this existed: a customer clicking "send me
    a code" and getting a Django traceback with the settings, the database URL and
    the whole environment printed down the page.

    A single process_exception hook rather than try/except in each view: the next
    step adds views too, and a guard that must be remembered is a guard that will
    be forgotten.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if not isinstance(exception, RedisError):
            return None
        # Loud in the log and in error tracking, quiet and honest on screen.
        logger.error(
            "cache unavailable on %s (%s)", redact_url(request.path), type(exception).__name__
        )
        return render(request, "503.html", status=503)


class PersianUnlessChosen:
    """The panel opens in Persian whatever the browser claims to prefer.

    `LocaleMiddleware` negotiates in order: language cookie, then the browser's
    Accept-Language, then LANGUAGE_CODE. Windows ships an English browser, so
    without this the operator of a Persian shop opens the panel and finds it in
    English — and the language switch the owner asked for would read as a bug
    rather than a choice.

    Dropping the header only when no cookie is set is what keeps the switch
    working: once the operator picks a language, `set_language` writes the
    cookie, this stands aside, and their choice wins on every later request.

    The choice is the panel's alone. The shop has one language, and with the
    cookie read everywhere a panel switched to English printed the product
    page's delivery type and warranty in English as well.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith(reverse("admin:index")):
            request.COOKIES.pop(settings.LANGUAGE_COOKIE_NAME, None)
        if settings.LANGUAGE_COOKIE_NAME not in request.COOKIES:
            request.META.pop("HTTP_ACCEPT_LANGUAGE", None)
        return self.get_response(request)

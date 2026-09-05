"""Turn an infrastructure outage into an honest page instead of a traceback."""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


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
        logger.error("cache unavailable on %s: %s", request.path, exception)
        return render(request, "503.html", status=503)

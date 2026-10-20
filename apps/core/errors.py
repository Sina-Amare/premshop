"""Error pages Django would render in English, or without the shop around them."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseServerError
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse

from apps.core import support


def server_error(request: HttpRequest) -> HttpResponse:
    """500.html without a request context, so no context processor runs: the
    database or the session may be what failed, and this page must still
    render. What it prints about support is passed in and touches no table."""
    return HttpResponseServerError(render_to_string("500.html", {"support": support.details()}))


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """Django's own view renders without a request, which would leave the
    shell without its support details and its departments. A form in the
    panel fails here too, and its way back is the panel."""
    in_panel = request.path.startswith(reverse("admin:index"))
    return render(request, "403_csrf.html", {"in_panel": in_panel}, status=403)

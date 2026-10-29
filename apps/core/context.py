"""Shared support and site configuration for page templates."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest

from apps.core import support


def shell(request: HttpRequest) -> dict[str, object]:
    return {
        "demo_catalog": settings.DEMO_CATALOG,
        "support": support.details(),
        "site_url": settings.SITE_URL,
    }

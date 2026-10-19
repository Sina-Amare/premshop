"""Fixtures for every test in the project."""

import os
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import cache
from playwright.sync_api import Browser, Error, sync_playwright


@pytest.fixture(autouse=True)
def clean_cache(settings: Any) -> Iterator[None]:
    """Every test runs against an empty in-memory cache, never a real Redis.

    Login codes, rate-limit counters and the /healthz cache probe all use the
    cache. Two things follow. A limit spent in one test would lock out the next,
    and the failure would look like a bug in the code under test. And CI has a
    Postgres service but no Redis, so any test that reaches the real backend passes
    on a developer machine and fails in the gate — which is exactly what happened
    for the first seventeen red runs of this project.
    """
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    """Playwright's Chromium, launched once per test module.

    Without Chromium a browser test skips, unless REQUIRE_BROWSER is set. CI and
    scripts/check.py set it, because there a skipped browser test is a gate that
    never ran and still reports green.
    """
    with pytest.MonkeyPatch.context() as patch, sync_playwright() as playwright:
        # The sync API runs an event loop, and Django then refuses ORM calls
        # "from an async context". Django documents this flag for exactly this.
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        try:
            chromium = playwright.chromium.launch()
        except Error as exc:
            if os.environ.get("REQUIRE_BROWSER"):
                pytest.fail("REQUIRE_BROWSER is set, so a browser test may not skip", pytrace=False)
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        yield chromium
        chromium.close()

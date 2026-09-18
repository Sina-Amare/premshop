"""Fixtures for every test in the project."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import cache


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

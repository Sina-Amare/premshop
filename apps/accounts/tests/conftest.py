"""Test-wide fixtures for the accounts app."""

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clean_cache(settings):
    """OTP codes and rate-limit counters live in the cache, so every test must
    start from an empty one — otherwise a limit spent in one test locks out the
    next, and the failure looks like a bug in the code under test.

    locmem rather than the real Redis: the suite must run in CI, which has a
    Postgres service and no Redis, and a test that needs a daemon running is a
    test people learn to skip.
    """
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def fast_password_hashing(settings):
    """PBKDF2 at a million iterations costs ~700ms per check, by design.

    That is correct in production and pointless in tests: the suite pays it on
    every login, and a suite slow enough to skip is a suite that stops catching
    things. Nothing here asserts on hashing cost, so the fast hasher changes no
    outcome — only the wait.
    """
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

"""Test-wide fixtures for the accounts app."""

import pytest


@pytest.fixture(autouse=True)
def fast_password_hashing(settings):
    """PBKDF2 at a million iterations costs ~700ms per check, by design.

    That is correct in production and pointless in tests: the suite pays it on
    every login, and a suite slow enough to skip is a suite that stops catching
    things. Nothing here asserts on hashing cost, so the fast hasher changes no
    outcome — only the wait.
    """
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

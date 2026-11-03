"""Production's headers, cookies and panel address, read from prod.py itself."""

from __future__ import annotations

import importlib
import os

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.urls import clear_url_caches, reverse

import config.urls
from apps.core.tests.test_site_url import PRODUCTION, load_prod

PASSWORD = "a long enough passphrase"  # noqa: S105 — a fixture value, not a credential
# What nginx tells Django about the visitor's connection.
HTTPS = {"HTTP_X_FORWARDED_PROTO": "https"}


def route() -> None:
    importlib.reload(config.urls)
    clear_url_caches()


@pytest.fixture
def production(reload_base):
    """This test's settings switched to prod.py's security settings and panel path."""
    reload_base(**PRODUCTION, SITE_URL="https://premshop.ir")
    prod = load_prod()
    chosen = {
        name: getattr(prod, name)
        for name in dir(prod)
        if name.startswith(("SECURE_", "SESSION_COOKIE_", "CSRF_COOKIE_"))
        or name in {"X_FRAME_OPTIONS", "ADMIN_PATH"}
    }
    with override_settings(**chosen):
        route()
        yield prod
    route()


@pytest.mark.django_db
def test_security_headers_present(production, client, settings):
    page = client.get(reverse("home"), **HTTPS)

    assert page.status_code == 200
    # An hour while the shop is new, so a certificate mistake locks visitors out
    # for an hour rather than a year; the launch step raises it.
    assert page["Strict-Transport-Security"] == "max-age=3600; includeSubDomains"
    assert page["X-Content-Type-Options"] == "nosniff"
    assert page["Referrer-Policy"] == "same-origin"
    assert page["X-Frame-Options"] == "DENY"

    get_user_model().objects.create_user("panel@example.test", PASSWORD, is_staff=True)
    form = client.get(reverse("admin:login"), **HTTPS)
    signed_in = client.post(
        reverse("admin:login"), {"username": "panel@example.test", "password": PASSWORD}, **HTTPS
    )
    assert signed_in.status_code == 302
    for cookie in (
        form.cookies[settings.CSRF_COOKIE_NAME],
        signed_in.cookies[settings.SESSION_COOKIE_NAME],
    ):
        assert cookie["secure"] and cookie["httponly"], cookie.key
        assert cookie["samesite"] == "Lax", cookie.key


@pytest.mark.django_db
def test_admin_default_path_404(production, client):
    assert production.ADMIN_PATH != "admin/"
    assert client.get("/admin/", **HTTPS).status_code == 404
    assert client.get("/admin/login/", **HTTPS).status_code == 404

    assert reverse("admin:index") == f"/{production.ADMIN_PATH}"
    assert client.get(reverse("admin:index"), **HTTPS)["Location"].startswith(
        reverse("admin:login")
    )
    sign_in = client.get(reverse("admin:login"), **HTTPS)
    assert sign_in.status_code == 200
    assert 'name="password"' in sign_in.content.decode()


def test_the_launch_step_can_raise_hsts_from_the_environment(reload_base):
    reload_base(**PRODUCTION, SITE_URL="https://premshop.ir", SECURE_HSTS_SECONDS="31536000")
    assert load_prod().SECURE_HSTS_SECONDS == 31536000


@pytest.mark.parametrize("value", [None, "", "admin/", "Admin/", "/panel/", "panel", "a/b/"])
def test_production_refuses_to_start_without_a_non_default_admin_path(reload_base, value):
    reload_base(**PRODUCTION, SITE_URL="https://premshop.ir")
    if value is None:
        del os.environ["ADMIN_PATH"]
    else:
        os.environ["ADMIN_PATH"] = value
    with pytest.raises(ImproperlyConfigured, match="ADMIN_PATH"):
        load_prod()

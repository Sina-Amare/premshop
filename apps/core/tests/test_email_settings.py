"""The email configuration sits on the login path, so its guards are tested.

Every failure covered here is silent in production rather than loud: Django's SMTP
backend connects anonymously when the password is blank (the relay then rejects
every message), raises ValueError only at send time if both TLS flags are set, and
waits forever by default when a connection blackholes. None of those surface as a
crash at boot, so each one gets a test rather than a comment.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

import config.settings.base as base

# SMTP2GO speaks STARTTLS on these and implicit TLS on the rest (its own panel text).
STARTTLS_PORTS = (2525, 8025, 587, 80, 25)
IMPLICIT_TLS_PORTS = (465, 8465, 443)


@pytest.fixture
def reload_base():
    """Re-import base.py under a patched environment, then put the world back.

    monkeypatch is not usable here: its teardown runs after this fixture's, so the
    restoring reload would still see the patched environment.
    """
    original = dict(os.environ)

    def _reload(**overrides: str):
        os.environ.update(overrides)
        return importlib.reload(base)

    yield _reload

    os.environ.clear()
    os.environ.update(original)
    sys.modules.pop("config.settings.prod", None)
    importlib.reload(base)


@pytest.mark.parametrize("port", STARTTLS_PORTS)
def test_starttls_ports_negotiate_upward(reload_base, port):
    reloaded = reload_base(EMAIL_PORT=str(port))
    assert reloaded.EMAIL_USE_TLS is True
    assert reloaded.EMAIL_USE_SSL is False


@pytest.mark.parametrize("port", IMPLICIT_TLS_PORTS)
def test_implicit_tls_ports_wrap_the_socket(reload_base, port):
    reloaded = reload_base(EMAIL_PORT=str(port))
    assert reloaded.EMAIL_USE_SSL is True
    assert reloaded.EMAIL_USE_TLS is False


@pytest.mark.parametrize("port", STARTTLS_PORTS + IMPLICIT_TLS_PORTS)
def test_the_two_encryption_flags_are_never_both_set(reload_base, port):
    """Django raises ValueError for that pair at send time — i.e. on a real login."""
    reloaded = reload_base(EMAIL_PORT=str(port))
    assert not (reloaded.EMAIL_USE_TLS and reloaded.EMAIL_USE_SSL)


@pytest.mark.parametrize("port", STARTTLS_PORTS + IMPLICIT_TLS_PORTS)
def test_encryption_is_on_for_every_port_in_the_fallback_ladder(reload_base, port):
    """No port in the ladder may ever send a login code in the clear."""
    reloaded = reload_base(EMAIL_PORT=str(port))
    assert reloaded.EMAIL_USE_TLS or reloaded.EMAIL_USE_SSL


def test_a_hung_relay_cannot_pin_a_worker_forever():
    assert settings.EMAIL_TIMEOUT, "Django's own default is None: no timeout at all"
    assert settings.EMAIL_TIMEOUT <= 30


def test_the_from_address_is_at_the_verified_sender_domain():
    assert settings.DEFAULT_FROM_EMAIL.strip().rstrip(">").endswith("@premshop.ir")


def test_production_refuses_to_boot_without_relay_credentials(reload_base):
    reload_base(EMAIL_HOST_PASSWORD="", ALLOWED_HOSTS="premshop.ir")
    with pytest.raises(ImproperlyConfigured, match="EMAIL_HOST_PASSWORD"):
        import config.settings.prod as prod

        importlib.reload(prod)


def test_production_refuses_a_from_address_the_relay_would_reject(reload_base):
    reload_base(ALLOWED_HOSTS="premshop.ir", DEFAULT_FROM_EMAIL="PremShop <hi@gmail.com>")
    with pytest.raises(ImproperlyConfigured, match="premshop.ir"):
        import config.settings.prod as prod

        importlib.reload(prod)


def test_the_machine_hostname_never_reaches_a_customers_inbox():
    """Django defaults the Message-ID domain and the EHLO name to socket.getfqdn().

    Regression: an un-pinned DNS_NAME stamped the developer's laptop hostname into
    the Message-ID of the first real send.
    """
    from django.core.mail import EmailMessage
    from django.core.mail.utils import DNS_NAME

    assert DNS_NAME.get_fqdn() == "premshop.ir"
    assert EmailMessage(to=["a@b.test"]).message()["Message-ID"].endswith("@premshop.ir>")

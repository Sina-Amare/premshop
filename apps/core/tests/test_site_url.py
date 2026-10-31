"""SITE_URL is the base of every absolute address the shop prints, so production needs it.

A canonical link, a preview image or an email link built on the request's Host
header points wherever the client said it was; built on a missing setting it
points at a development address, and nothing on the page looks broken.
"""

from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

# Every other check in prod.py, satisfied, so a failure here can only be the one a
# test breaks and not whichever variable the machine lacks.
PRODUCTION = {
    "ALLOWED_HOSTS": "premshop.ir",
    "EMAIL_HOST_USER": "relay-user",
    "EMAIL_HOST_PASSWORD": "relay-pass",
    "DEFAULT_FROM_EMAIL": "PremShop <support@premshop.ir>",
    "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "FIELD_ENCRYPTION_KEY_FALLBACKS": "",
    "SENTRY_DSN": "",
}


def load_prod():
    import config.settings.prod as prod

    return importlib.reload(prod)


@pytest.mark.parametrize(
    "broken",
    [
        {"FIELD_ENCRYPTION_KEY": ""},
        {"FIELD_ENCRYPTION_KEY": "not-a-fernet-key"},
        {"FIELD_ENCRYPTION_KEY_FALLBACKS": "not-a-fernet-key"},
        {"FIELD_ENCRYPTION_KEY_FALLBACKS": PRODUCTION["FIELD_ENCRYPTION_KEY"]},
    ],
)
def test_production_refuses_to_boot_without_usable_field_keys(reload_base, broken):
    reload_base(**{**PRODUCTION, **broken}, SITE_URL="https://premshop.ir")
    with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEY"):
        load_prod()


def test_production_boots_with_a_distinct_fallback_during_a_rotation(reload_base):
    fallback = Fernet.generate_key().decode()
    reload_base(
        **{**PRODUCTION, "FIELD_ENCRYPTION_KEY_FALLBACKS": fallback},
        SITE_URL="https://premshop.ir",
    )
    assert load_prod().field_keys == [PRODUCTION["FIELD_ENCRYPTION_KEY"], fallback]


def test_production_refuses_to_boot_without_site_url(reload_base):
    reload_base(**PRODUCTION, SITE_URL="")
    with pytest.raises(ImproperlyConfigured, match="SITE_URL"):
        load_prod()


def test_production_refuses_a_site_url_that_is_not_https(reload_base):
    reload_base(**PRODUCTION, SITE_URL="http://premshop.ir")
    with pytest.raises(ImproperlyConfigured, match="https"):
        load_prod()


def test_production_boots_with_one_and_keeps_scheme_and_host_only(reload_base):
    reload_base(**PRODUCTION, SITE_URL="https://PremShop.ir/")

    assert load_prod().SITE_URL == "https://premshop.ir"


@pytest.mark.parametrize(
    "value", ["premshop.ir", "https://premshop.ir/shop/", "https://premshop.ir/?a=1", "ftp://x.ir"]
)
def test_a_site_url_with_a_path_or_no_scheme_is_refused_at_startup(reload_base, value):
    """A path after the host would be doubled or lost in every link built on it."""
    with pytest.raises(ImproperlyConfigured, match="SITE_URL"):
        reload_base(SITE_URL=value)


def test_development_has_an_address_without_being_told_one(reload_base):
    assert reload_base(SITE_URL="").SITE_URL == "http://127.0.0.1:8000"

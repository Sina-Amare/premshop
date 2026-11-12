"""The SMS settings sit on the only way a customer signs in, so a wrong one refuses
to boot rather than failing every code request after the site is up."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.core.tests.test_site_url import PRODUCTION, load_prod


@pytest.mark.parametrize(
    ("override", "named"),
    [({"SMS_IR_API_KEY": ""}, "SMS_IR_API_KEY"), ({"SMS_BACKEND": "fake"}, "SMS_BACKEND")],
)
def test_production_refuses_to_boot_without_a_real_sender(reload_base, override, named):
    reload_base(**{**PRODUCTION, "SITE_URL": "https://premshop.ir", **override})

    with pytest.raises(ImproperlyConfigured, match=named):
        load_prod()


def test_production_boots_with_a_key_and_a_line(reload_base):
    reload_base(**PRODUCTION, SITE_URL="https://premshop.ir")

    assert load_prod().SMS_IR_API_KEY == PRODUCTION["SMS_IR_API_KEY"]


@pytest.mark.parametrize("name", ["SMS_IR_TEMPLATE_ID", "SMS_IR_LINE_NUMBER"])
def test_an_sms_id_that_is_not_a_number_is_refused(reload_base, name):
    with pytest.raises(ImproperlyConfigured, match=name):
        reload_base(**{**PRODUCTION, name: "5000 3268"})


def test_an_unknown_sms_backend_is_refused(reload_base):
    with pytest.raises(ImproperlyConfigured, match="SMS_BACKEND"):
        reload_base(SMS_BACKEND="smsr")


def test_a_key_with_neither_a_template_nor_a_line_is_refused(reload_base):
    with pytest.raises(ImproperlyConfigured, match="SMS_IR_TEMPLATE_ID or SMS_IR_LINE_NUMBER"):
        reload_base(**{**PRODUCTION, "SMS_IR_LINE_NUMBER": ""})

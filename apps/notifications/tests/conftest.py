import pytest

from apps.orders.tests.conftest import buyer as buyer
from apps.orders.tests.conftest import encryption_keys as encryption_keys
from apps.orders.tests.conftest import plan as plan
from apps.orders.tests.test_purchase_record import policies as policies


@pytest.fixture(autouse=True)
def mail_baseline(settings, clean_mail_accounting):
    settings.EMAIL_QUOTA_RESET_DATE = "2026-11-01"
    settings.EMAIL_QUOTA_STARTING_USAGE = 0

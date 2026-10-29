from io import StringIO

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.core.management import call_command
from django.db import connection

from apps.catalog.models import Plan
from apps.core.fields import EncryptedTextField, key_id
from apps.core.observability import sentry_options
from apps.orders.models import OrderItem
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def email_input(plan):
    Plan.objects.filter(pk=plan.pk).update(
        input_type="email", requires_customer_input=True, max_quantity=1
    )


def raw_input(item):
    with connection.cursor() as cursor:
        cursor.execute("SELECT customer_input FROM orders_orderitem WHERE id = %s", [item.pk])
        return cursor.fetchone()[0]


def test_ciphertext_is_randomized_and_readable_outside_the_field(buyer, plan, settings):
    item = place_order(
        activation_consent=True, user=buyer, lines=[OrderLine(plan, 1, "target@example.test")]
    ).items.get()
    raw = raw_input(item)
    prefix, token = raw.split("$", 1)
    assert prefix == key_id(settings.FIELD_ENCRYPTION_KEY)
    assert "target@example.test" not in raw
    assert (
        Fernet(settings.FIELD_ENCRYPTION_KEY.encode()).decrypt(token.encode())
        == b"target@example.test"
    )
    item.save(update_fields=["customer_input"])
    assert raw_input(item) != raw
    item.refresh_from_db()
    assert item.customer_input == "target@example.test"


def test_old_key_reads_and_rotation_rewrites_only_old_ciphertext(buyer, plan, settings):
    item = place_order(
        activation_consent=True, user=buyer, lines=[OrderLine(plan, 1, "target@example.test")]
    ).items.get()
    old_key = settings.FIELD_ENCRYPTION_KEY
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEY_FALLBACKS = [old_key]
    item.refresh_from_db()
    assert item.customer_input == "target@example.test"
    out = StringIO()
    call_command("reencrypt_fields", batch_size=1, stdout=out)
    assert raw_input(item).startswith(key_id(settings.FIELD_ENCRYPTION_KEY) + "$")
    before = raw_input(item)
    call_command("reencrypt_fields", batch_size=1, stdout=out)
    assert raw_input(item) == before
    settings.FIELD_ENCRYPTION_KEY_FALLBACKS = []
    item.refresh_from_db()
    assert item.customer_input == "target@example.test"


def test_missing_key_and_corrupt_ciphertext_fail_without_disclosing_values(buyer, plan, settings):
    item = place_order(
        activation_consent=True, user=buyer, lines=[OrderLine(plan, 1, "target@example.test")]
    ).items.get()
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with pytest.raises(ImproperlyConfigured) as exc:
        item.refresh_from_db()
    assert "target@example.test" not in str(exc.value)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE orders_orderitem SET customer_input = %s WHERE id = %s",
            ["bad-ciphertext", item.pk],
        )
    with pytest.raises(ImproperlyConfigured) as exc:
        item.refresh_from_db()
    assert "bad-ciphertext" not in str(exc.value)


@pytest.mark.parametrize("lookup", ["exact", "contains", "in"])
def test_searching_encrypted_values_is_refused(lookup):
    with pytest.raises(FieldError):
        OrderItem.objects.filter(**{f"customer_input__{lookup}": "secret"})
    assert OrderItem.objects.filter(customer_input__isnull=True).count() == 0


def test_migrations_never_serialize_keys_and_error_reports_have_no_local_variables(settings):
    field = EncryptedTextField()
    assert settings.FIELD_ENCRYPTION_KEY not in repr(field.deconstruct())
    assert sentry_options(dsn="", environment="test")["include_local_variables"] is False

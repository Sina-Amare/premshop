"""The mobile number is the account's identity: one normal form, unique in the database."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse

from apps.accounts.backends import CODE_SIGN_IN
from apps.accounts.models import User

pytestmark = pytest.mark.django_db

BEFORE = [("accounts", "0002_labels_from_the_glossary")]
AFTER = [("accounts", "0003_phone_identity")]
UNUSABLE = "!"  # noqa: S105 - Django's mark for an unusable password


def test_the_phone_is_the_username():
    assert User.USERNAME_FIELD == "phone"
    assert str(User.objects.create_user("x@example.test", phone="09120000003")) == "09120000003"


def test_two_spellings_of_one_number_are_one_account():
    User.objects.create_user(phone="+98 912 345 6789")

    with pytest.raises(ValidationError):
        User.objects.create_user(phone="۰۹۱۲۳۴۵۶۷۸۹")

    assert User.objects.get().phone == "09123456789"


def test_the_database_itself_refuses_a_second_account_for_a_number():
    User(phone="09123456789").save()

    with pytest.raises(IntegrityError), transaction.atomic():
        User(phone="09123456789").save()


def test_the_database_refuses_a_number_out_of_its_normal_form():
    """The manager normalises, but the panel, a shell or an import reach the table directly."""
    user = User.objects.create_user(phone="09123456789")

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(phone="+989123456789")


def test_an_account_needs_a_phone_or_an_email():
    with pytest.raises(ValueError):
        User.objects.create_user()


def test_accounts_without_a_number_do_not_collide():
    User.objects.create_user("a@example.test")
    User.objects.create_user("b@example.test")

    assert User.objects.filter(phone__isnull=True).count() == 2


def test_two_accounts_may_share_an_email():
    """Email is a contact address now, not a key: a family may share one."""
    User.objects.create_user("family@example.test", phone="09120000001")
    User.objects.create_user("family@example.test", phone="09120000002")

    assert User.objects.filter(email="family@example.test").count() == 2


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def back_to_the_latest():
    yield
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_the_migration_keeps_an_account_and_goes_back(back_to_the_latest):
    migrate(BEFORE).get_model("accounts", "User").objects.create(
        email="owner@example.test",
        password=UNUSABLE,
        phone="",
    )

    user = migrate(AFTER).get_model("accounts", "User").objects.get()
    assert user.phone is None and user.email == "owner@example.test"

    assert migrate(BEFORE).get_model("accounts", "User").objects.get().phone == ""


@pytest.mark.django_db(transaction=True)
def test_going_back_is_refused_while_an_account_has_no_email(back_to_the_latest):
    migrate(AFTER).get_model("accounts", "User").objects.create(
        phone="09120000004", password=UNUSABLE
    )

    with pytest.raises(RuntimeError, match="no email"):
        migrate(BEFORE)


@pytest.mark.django_db(transaction=True)
def test_going_back_is_refused_while_two_accounts_share_an_email(back_to_the_latest):
    users = migrate(AFTER).get_model("accounts", "User").objects
    users.create(phone="09120000005", email="shared@example.test", password=UNUSABLE)
    users.create(phone="09120000006", email="Shared@Example.test", password=UNUSABLE)

    with pytest.raises(RuntimeError, match="share an email"):
        migrate(BEFORE)


def test_moving_an_account_to_a_new_number_signs_out_the_old_one(admin_client, client):
    customer = User.objects.create_user(phone="09121112233", is_verified=True)
    client.force_login(customer, backend=CODE_SIGN_IN)
    assert client.get(reverse("account")).status_code == 200

    moved = admin_client.post(
        reverse("admin:accounts_user_change", args=[customer.pk]),
        {"phone": "0912 444 5566", "is_active": "on"},
    )

    assert moved.status_code == 302
    customer.refresh_from_db()
    assert customer.phone == "09124445566"
    assert not customer.is_verified, "the new number is proven by its first code"
    assert client.get(reverse("account")).status_code == 302

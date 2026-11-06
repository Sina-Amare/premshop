from io import StringIO
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.fields import key_id
from apps.core.observability import scrub_event
from apps.orders import models, services

pytestmark = pytest.mark.django_db
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


@pytest.fixture
def order(buyer):
    return create_order(buyer)


def create_order(buyer):
    order = models.Order.objects.create(user=buyer, subtotal=0, total_amount=0)
    models.OrderSupportIdentifier.objects.create(order=order)
    return order


@pytest.fixture
def operator():
    user = User.objects.create_user("operator@example.test", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_order"))
    return user


def raw_token(order):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT support_token FROM orders_ordersupportidentifier WHERE order_id = %s",
            [order.pk],
        )
        return cursor.fetchone()[0]


def compare(order, operator, support_token):
    return services.match_support_token(
        order_id=order.pk, actor=operator, support_token=support_token
    )


def test_identifier_is_random_unambiguous_stable_and_encrypted(order, buyer, settings):
    second = create_order(buyer)
    original = order.support_identifier.support_token
    assert len(original) == 12 and set(original) <= set(ALPHABET)
    assert original != second.support_identifier.support_token
    assert order.support_identifier.grouped_token == " ".join(
        original[n : n + 4] for n in (0, 4, 8)
    )
    raw = raw_token(order)
    prefix, ciphertext = raw.split("$", 1)
    assert prefix == key_id(settings.FIELD_ENCRYPTION_KEY)
    assert original not in raw
    assert (
        Fernet(settings.FIELD_ENCRYPTION_KEY.encode()).decrypt(ciphertext.encode()).decode()
        == original
    )
    order.refresh_from_db()
    assert order.support_identifier.support_token == original


def test_comparison_is_per_order_normalizes_groups_and_changes_nothing(order, buyer, operator):
    second = create_order(buyer)
    before = models.Order.objects.filter(pk=order.pk).values().get()
    for supplied in (
        order.support_identifier.support_token,
        order.support_identifier.grouped_token,
        order.support_identifier.grouped_token.lower().replace(" ", "-"),
    ):
        assert compare(order, operator, supplied)
    for supplied in (second.support_identifier.support_token, "", "X" * 13, "0O1IL" * 3):
        assert not compare(order, operator, supplied)
    assert models.Order.objects.filter(pk=order.pk).values().get() == before
    assert not models.OrderItemEvent.objects.exists()
    with pytest.raises(PermissionDenied):
        services.reveal_customer_input(order_id=order.pk, item_id=1, actor=operator, ip=None)


def test_replacement_is_owner_only_and_stale_requests_cannot_replace_again(order, buyer, operator):
    original = order.support_identifier.support_token
    changed = services.replace_support_token(
        order_id=order.pk, actor=buyer, expected_support_token=original
    )
    assert changed.support_token != original
    assert not compare(order, operator, original)
    assert compare(order, operator, changed.support_token)
    with pytest.raises(ValidationError):
        services.replace_support_token(
            order_id=order.pk, actor=buyer, expected_support_token=original
        )
    order.refresh_from_db()
    assert order.support_identifier.support_token == changed.support_token
    for actor in (operator, User.objects.create_user("stranger@example.test")):
        with pytest.raises(PermissionDenied):
            services.replace_support_token(
                order_id=order.pk,
                actor=actor,
                expected_support_token=order.support_identifier.support_token,
            )
    buyer.is_active = False
    with pytest.raises(PermissionDenied):
        services.replace_support_token(
            order_id=order.pk,
            actor=buyer,
            expected_support_token=order.support_identifier.support_token,
        )


def test_matching_service_refuses_customers_unpermitted_or_inactive_staff(order, buyer, operator):
    staff = User.objects.create_user("staff@example.test", is_staff=True)
    operator.is_active = False
    for actor in (buyer, staff, operator):
        with pytest.raises(PermissionDenied):
            compare(order, actor, order.support_identifier.support_token)


def test_customer_detail_and_replacement_require_ownership_post_confirmation_and_csrf(
    client, order, buyer, operator
):
    detail = reverse("order-detail", args=[order.order_number])
    replace = reverse("order-support-replace", args=[order.order_number])
    assert client.get(detail).status_code == 302
    assert client.post(replace).status_code == 302
    client.force_login(operator)
    assert client.get(detail).status_code == 404
    assert client.post(replace).status_code == 404
    client.force_login(buyer)
    response = client.get(detail)
    assert order.support_identifier.grouped_token in response.content.decode()
    assert response["Content-Encoding"] == "identity"
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"] == "same-origin"
    assert client.get(replace).status_code == 405
    data = {"confirm": "yes", "expected_support_token": order.support_identifier.support_token}
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(buyer)
    assert csrf.post(replace, data).status_code == 403
    assert client.post(replace).status_code == 400
    assert client.post(replace, data).status_code == 302
    assert client.post(replace, data).status_code == 400
    order.refresh_from_db()
    assert order.support_identifier.support_token != data["expected_support_token"]


def test_match_form_is_scoped_private_and_never_echoes_either_identifier(
    client, order, buyer, operator
):
    url = reverse("admin:orders_order_support_match", args=[order.pk])
    detail = reverse("admin:orders_order_change", args=[order.pk])
    client.force_login(operator)
    assert client.get(url).status_code == 405
    assert order.support_identifier.support_token not in client.get(detail).content.decode()
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(operator)
    assert (
        csrf.post(url, {"support_token": order.support_identifier.support_token}).status_code == 403
    )
    for supplied, message in (
        (order.support_identifier.support_token, "شناسه با این سفارش مطابقت دارد."),
        ("PRIVATE-BAD-INPUT", "شناسه با این سفارش مطابقت ندارد."),
    ):
        response = client.post(url, {"support_token": supplied})
        body = response.content.decode()
        assert response.status_code == 200 and message in body
        assert supplied not in body and order.support_identifier.support_token not in body
        assert "no-store" in response["Cache-Control"]
        assert response["Referrer-Policy"] == "no-referrer"
    from apps.orders.admin import OrderAdmin

    with patch.object(
        OrderAdmin, "has_view_permission", side_effect=lambda request, obj=None: obj is None
    ):
        assert (
            client.post(url, {"support_token": order.support_identifier.support_token}).status_code
            == 403
        )
    client.force_login(buyer)
    assert client.post(
        url, {"support_token": order.support_identifier.support_token}
    ).status_code in (302, 403)
    staff = User.objects.create_user("denied@example.test", is_staff=True)
    client.force_login(staff)
    with patch("apps.orders.admin.get_object_or_404") as lookup:
        assert (
            client.post(url, {"support_token": order.support_identifier.support_token}).status_code
            == 403
        )
        lookup.assert_not_called()


def test_identifier_stays_out_of_invoices_tracking_lists_and_error_reports(
    client, order, buyer, operator
):
    client.force_login(buyer)
    for url in (
        reverse("order-invoice", args=[order.order_number]),
        reverse("order-tracking", args=[order.tracking_token]),
        reverse("order-list"),
        reverse("account"),
    ):
        response = client.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        assert (
            order.support_identifier.support_token not in body
            and order.support_identifier.grouped_token not in body
        )
    client.force_login(operator)
    assert (
        order.support_identifier.grouped_token
        not in client.get(reverse("admin:orders_order_changelist")).content.decode()
    )
    event = scrub_event(
        {
            "request": {"data": {"support_token": order.support_identifier.support_token}},
            "extra": {
                "support_token": order.support_identifier.support_token,
                "expected_support_token": order.support_identifier.support_token,
            },
        }
    )
    assert order.support_identifier.support_token not in repr(event)


def test_encryption_tools_cover_support_identifiers_without_printing_them(order, settings):
    original = order.support_identifier.support_token
    old_key = settings.FIELD_ENCRYPTION_KEY
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEY_FALLBACKS = [old_key]
    output = StringIO()
    call_command("reencrypt_fields", stdout=output)
    assert raw_token(order).startswith(key_id(settings.FIELD_ENCRYPTION_KEY) + "$")
    settings.FIELD_ENCRYPTION_KEY_FALLBACKS = []
    call_command("verify_decrypt", stdout=output)
    assert "orders.OrderSupportIdentifier.support_token: 1 decrypted, 0 failed" in output.getvalue()
    assert original not in output.getvalue()
    order.refresh_from_db()
    assert order.support_identifier.support_token == original


def test_support_creation_is_atomic_with_purchase_and_identical_checkout_retry_keeps_it(
    buyer, plan
):
    from uuid import uuid4

    checkout_id = uuid4()
    order = services.place_order(
        user=buyer,
        lines=[services.OrderLine(plan, 1)],
        checkout_id=checkout_id,
        submission_digest="a" * 64,
    )
    support_token = order.support_identifier.support_token
    replay = services.place_order(
        user=buyer,
        lines=[services.OrderLine(plan, 1)],
        checkout_id=checkout_id,
        submission_digest="a" * 64,
    )
    assert replay.pk == order.pk
    assert replay.support_identifier.support_token == support_token
    assert models.OrderSupportIdentifier.objects.count() == 1


def test_failure_creating_identifier_rolls_back_the_purchase(buyer, plan):
    with patch.object(
        models.OrderSupportIdentifier.objects, "create", side_effect=RuntimeError("Write failed")
    ):
        with pytest.raises(RuntimeError, match="Write failed"):
            services.place_order(user=buyer, lines=[services.OrderLine(plan, 1)])
    assert not models.Order.objects.exists()
    assert not models.OrderItem.objects.exists()


def test_missing_key_preserves_order_reads_and_shows_unavailable_without_controls(
    client, order, buyer, operator, settings
):
    support_token = order.support_identifier.support_token
    settings.FIELD_ENCRYPTION_KEY = ""
    order.refresh_from_db()
    assert order.total_amount == 0
    client.force_login(buyer)
    detail = reverse("order-detail", args=[order.order_number])
    response = client.get(detail)
    body = response.content.decode()
    assert response.status_code == 200 and "شناسه‌ی پشتیبانی در دسترس نیست." in body
    assert support_token not in body
    assert "data-copy-support" not in body and 'name="expected_support_token"' not in body
    for name, args in (("order-list", []), ("order-invoice", [order.order_number])):
        assert client.get(reverse(name, args=args)).status_code == 200
    assert (
        client.post(
            reverse("order-support-replace", args=[order.order_number]),
            {"confirm": "yes", "expected_support_token": support_token},
        ).status_code
        == 503
    )
    client.force_login(operator)
    assert client.get(reverse("admin:orders_order_changelist")).status_code == 200
    assert client.get(reverse("admin:orders_order_change", args=[order.pk])).status_code == 200
    response = client.post(
        reverse("admin:orders_order_support_match", args=[order.pk]),
        {"support_token": support_token},
    )
    assert response.status_code == 503
    assert "بررسی شناسه در دسترس نیست؛ دوباره تلاش کنید." in response.content.decode()


def test_item_order_lookup_does_not_read_support_identifier(buyer, plan, settings):
    order = services.place_order(user=buyer, lines=[services.OrderLine(plan, 1)])
    item = models.OrderItem.objects.only("order_id").get(order=order)
    settings.FIELD_ENCRYPTION_KEY = ""
    assert item.order.pk == order.pk


def test_comparison_failure_and_confirmation_email_contain_no_identifier(
    client, order, operator, settings, caplog
):
    from apps.notifications.services import confirmation_context, render_confirmation

    assert order.support_identifier.support_token not in repr(confirmation_context(order))
    assert order.support_identifier.support_token not in repr(render_confirmation(order))
    assert order.support_identifier.grouped_token not in repr(render_confirmation(order))
    settings.DEBUG = False
    client.raise_request_exception = False
    client.force_login(operator)
    with patch(
        "apps.orders.admin.match_support_token", side_effect=RuntimeError("Comparison unavailable")
    ):
        response = client.post(
            reverse("admin:orders_order_support_match", args=[order.pk]),
            {"support_token": order.support_identifier.support_token},
        )
    assert response.status_code == 500
    assert order.support_identifier.support_token not in response.content.decode() + caplog.text
    assert order.support_identifier.grouped_token not in response.content.decode() + caplog.text


@pytest.mark.django_db(transaction=True)
def test_concurrent_replacements_only_rotate_the_confirmed_identifier_once(
    order, buyer, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from threading import Event

    from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock

    original = order.support_identifier.support_token
    save = models.OrderSupportIdentifier.save
    entered, release, pids = Event(), Event(), Queue()

    def held(instance, *args, **kwargs):
        if kwargs.get("update_fields") == ["support_token"]:
            entered.set()
            assert release.wait(10)
        return save(instance, *args, **kwargs)

    monkeypatch.setattr(models.OrderSupportIdentifier, "save", held)

    def replace():
        return services.replace_support_token(
            order_id=order.pk, actor=buyer, expected_support_token=original
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, replace)
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, replace)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        changed = first.result(timeout=10)
        assert second.result(timeout=10) is None
    order.refresh_from_db()
    assert changed.support_token == order.support_identifier.support_token != original

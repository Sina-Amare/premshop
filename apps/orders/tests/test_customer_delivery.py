import importlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from queue import Queue
from unittest.mock import patch

import pytest
import time_machine
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core import email_log
from apps.core.fields import EncryptedTextField
from apps.orders import models, services
from apps.orders.tests.test_delivery import LINE, NOTE, VALUE, deliver, queued, staff_with
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection
from apps.orders.tests.test_replacement import claim, replace

pytestmark = pytest.mark.django_db(transaction=True)
SESSION = "buyer-session-one"


def proof():
    return importlib.import_module("apps.orders.proof")


def code_from_mail():
    return re.search(r"(?<!\d)\d{6}(?!\d)", mail.outbox[-1].body).group()


def request_code(order, buyer, session=SESSION, ip="127.0.0.1"):
    proof().request_code(order_id=order.pk, actor=buyer, session_key=session, ip=ip)
    return code_from_mail()


def confirm(order, buyer, code, session=SESSION):
    return proof().confirm_code(order_id=order.pk, actor=buyer, session_key=session, code=code)


def verified(order, buyer, session=SESSION):
    assert confirm(order, buyer, request_code(order, buyer, session), session)


def reveal(order, item, buyer, session=SESSION):
    return services.reveal_delivery(
        order_id=order.pk,
        item_id=item.pk,
        actor=buyer,
        ip="127.0.0.1",
        customer_session=session,
    )


def delivered(buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    deliver(item, operator, note=NOTE)
    return order, item


def urls(order, item):
    return (
        reverse("order-delivery-code", args=[order.order_number]),
        reverse("order-delivery-confirm", args=[order.order_number]),
        reverse("order-delivery-reveal", args=[order.order_number, item.pk]),
    )


def test_frozen_email_proof_reveals_current_delivery_and_logs_before_decrypting(
    buyer, plan, operator, mailoutbox
):
    order, item = delivered(buyer, plan, operator)
    User.objects.filter(pk=buyer.pk).update(email="new-holder@example.test")
    buyer.refresh_from_db()
    with pytest.raises(PermissionDenied):
        reveal(order, item, buyer)
    code = request_code(order, buyer)
    assert mailoutbox[-1].to == [order.placement_email]
    assert VALUE not in mailoutbox[-1].body and NOTE not in mailoutbox[-1].body
    assert confirm(order, buyer, code)
    original, seen = EncryptedTextField.from_db_value, []

    def watched(self, value, expression, conn):
        seen.append(models.CredentialAccessLog.objects.filter(source="customer").count())
        return original(self, value, expression, conn)

    with patch.object(EncryptedTextField, "from_db_value", watched):
        content = reveal(order, item, buyer)
    assert content.lines == (LINE,) and content.note == NOTE and seen == [1, 1]
    row = models.CredentialAccessLog.objects.get()
    assert (row.source, row.target, row.user_id) == ("customer", "delivery", buyer.pk)
    assert VALUE not in repr(content)
    assert not confirm(order, buyer, code)
    assert VALUE in reveal(order, item, buyer).lines[0].value
    assert models.CredentialAccessLog.objects.count() == 2


@pytest.mark.parametrize("status", list(models.OrderItem.Status.values))
def test_customer_state_gate_does_not_change_staff_reveal(buyer, plan, operator, status):
    order, item = delivered(buyer, plan, operator)
    verified(order, buyer)
    changes = {"status": status}
    if status in ("CANCELLED", "REFUNDED"):
        changes["cancel_reason"] = "warranty_refund"
    models.OrderItem.objects.filter(pk=item.pk).update(**changes)
    if status in ("DELIVERED", "REPLACEMENT_REQUESTED"):
        assert reveal(order, item, buyer).lines == (LINE,)
    else:
        assert reveal(order, item, buyer) is None
        assert not models.CredentialAccessLog.objects.exists()
    assert services.reveal_delivery(
        order_id=order.pk, item_id=item.pk, actor=operator, ip=None
    ).lines == (LINE,)
    assert models.CredentialAccessLog.objects.latest("pk").source == "panel"


def test_proof_is_bound_to_owner_order_and_session(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    other = User.objects.create_user("other@example.test")
    code = request_code(order, buyer)
    with pytest.raises(PermissionDenied):
        request_code(order, other)
    assert not confirm(order, buyer, code, "other-session")
    assert confirm(order, buyer, code)
    with pytest.raises(PermissionDenied):
        reveal(order, item, buyer, "other-session")
    second = services.place_order(user=buyer, lines=[services.OrderLine(plan, 1)])
    with pytest.raises(PermissionDenied):
        reveal(second, item, buyer)
    User.objects.filter(pk=buyer.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        reveal(order, item, buyer)


def test_blank_frozen_email_does_not_use_a_later_contact(buyer, plan, operator, mailoutbox):
    order, item = delivered(buyer, plan, operator)
    models.Order.objects.filter(pk=order.pk).update(placement_email="")
    with pytest.raises(ValidationError):
        request_code(order, buyer)
    assert not mailoutbox
    client = Client()
    client.force_login(buyer)
    body = client.get(reverse("order-detail", args=[order.order_number])).content.decode()
    assert "data-delivery-recovery" in body and "order-delivery-code" not in body
    assert VALUE not in body


def test_codes_and_proofs_expire_and_five_wrong_guesses_spend_a_code(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    now = timezone.now()
    with time_machine.travel(now, tick=False):
        code = request_code(order, buyer)
        for _ in range(5):
            assert not confirm(order, buyer, "not-a-code")
        assert not confirm(order, buyer, code)
    with time_machine.travel(now + timedelta(seconds=61), tick=False):
        code = request_code(order, buyer)
    with time_machine.travel(now + timedelta(seconds=662), tick=False):
        assert not confirm(order, buyer, code)
        code = request_code(order, buyer)
        assert confirm(order, buyer, code)
    with time_machine.travel(now + timedelta(seconds=1263), tick=False):
        with pytest.raises(PermissionDenied):
            reveal(order, item, buyer)


def test_concurrent_confirmation_consumes_once(buyer, plan, operator):
    order, _ = delivered(buyer, plan, operator)
    code = request_code(order, buyer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda _: in_connection(Queue(), lambda: confirm(order, buyer, code)), range(2)
            )
        )
    assert sorted(outcomes) == [False, True]


def test_request_budgets_cover_account_order_and_ip(buyer, plan, operator):
    order, _ = delivered(buyer, plan, operator)
    second = services.place_order(user=buyer, lines=[services.OrderLine(plan, 1)])
    now = timezone.now()
    with time_machine.travel(now, tick=False):
        request_code(order, buyer)
        with pytest.raises(ValidationError):
            request_code(second, buyer)
    for minute in range(1, 5):
        with time_machine.travel(now + timedelta(minutes=minute * 2), tick=False):
            request_code(order, buyer)
    with time_machine.travel(now + timedelta(minutes=11), tick=False):
        with pytest.raises(ValidationError):
            request_code(second, buyer)
    cache.clear()
    for number in range(10):
        account = User.objects.create_user(f"buyer{number}@example.test")
        purchase = services.place_order(user=account, lines=[services.OrderLine(plan, 1)])
        request_code(purchase, account)
    with pytest.raises(ValidationError):
        request_code(order, buyer)


@pytest.mark.parametrize("failure", ["refused", "error", "cache"])
def test_failed_send_or_cache_grants_nothing(buyer, plan, operator, failure):
    order, item = delivered(buyer, plan, operator)
    target = (
        "apps.orders.proof.cache.set"
        if failure == "cache"
        else "apps.orders.proof.send_templated_email"
    )
    error = RuntimeError("transport failed")
    with patch(target, **({"return_value": 0} if failure == "refused" else {"side_effect": error})):
        with pytest.raises((ValidationError, RuntimeError)):
            request_code(order, buyer)
    with pytest.raises(PermissionDenied):
        reveal(order, item, buyer)


def test_newest_set_only_and_deleted_unreadable_states(buyer, plan, operator, settings):
    order, item = delivered(buyer, plan, operator)
    verified(order, buyer)
    claim(item, operator)
    replace(item, operator, note="new note")
    content = reveal(order, item, buyer)
    assert VALUE not in content.lines[0].value and content.note == "new note"
    before = models.CredentialAccessLog.objects.count()
    settings.FIELD_ENCRYPTION_KEY = ""
    with pytest.raises(ImproperlyConfigured):
        reveal(order, item, buyer)
    assert models.CredentialAccessLog.objects.count() == before
    models.Delivery.objects.filter(order_item=item).delete()
    assert reveal(order, item, buyer) is None


def test_order_gets_mask_without_loading_encrypted_fields_and_post_requires_proof(
    buyer, plan, operator
):
    order, item = delivered(buyer, plan, operator)
    client = Client()
    client.force_login(buyer)
    with patch.object(EncryptedTextField, "from_db_value", side_effect=AssertionError("decrypted")):
        # Support identifiers have their own logged-in display, independent of delivery masking.
        with patch("apps.orders.views.OrderSupportIdentifier.objects.filter") as identifiers:
            identifiers.return_value.first.return_value = None
            response = client.get(reverse("order-detail", args=[order.order_number]))
    assert VALUE not in response.content.decode() and NOTE not in response.content.decode()
    assert "data-delivery-masked" in response.content.decode()
    request_url, confirm_url, reveal_url = urls(order, item)
    assert client.post(reveal_url).status_code == 403
    assert client.post(request_url).status_code == 302
    code = code_from_mail()
    assert client.post(confirm_url, {"code": code}).status_code == 302
    response = client.post(reveal_url, HTTP_ACCEPT_ENCODING="gzip")
    assert response.status_code == 200 and VALUE in response.content.decode()
    assert response.headers["Content-Encoding"] == "identity"
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert (
        VALUE not in client.get(reverse("order-detail", args=[order.order_number])).content.decode()
    )


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_customer_actions_are_post_only_and_owner_only(buyer, plan, operator, method):
    order, item = delivered(buyer, plan, operator)
    client = Client()
    client.force_login(buyer)
    for url in urls(order, item):
        assert getattr(client, method)(url).status_code == 405
    client.force_login(User.objects.create_user("stranger@example.test"))
    for url in urls(order, item):
        response = client.post(url)
        assert response.status_code == 404 and VALUE not in response.content.decode()
        assert "no-store" in response.headers["Cache-Control"]
    assert not models.CredentialAccessLog.objects.exists()


def test_csrf_and_redaction_on_a_failed_reveal(buyer, plan, operator, settings, caplog):
    order, item = delivered(buyer, plan, operator)
    client = Client(enforce_csrf_checks=True)
    client.force_login(buyer)
    for url in urls(order, item):
        response = client.post(url, {"code": "123456"}, secure=True)
        assert response.status_code == 403 and "no-store" in response.headers["Cache-Control"]
    ordinary = Client(raise_request_exception=False)
    ordinary.force_login(buyer)
    verified(order, buyer, ordinary.session.session_key)
    settings.DEBUG = False
    with patch("apps.orders.services.DeliveryContent", side_effect=RuntimeError("render failed")):
        response = ordinary.post(urls(order, item)[2])
    assert response.status_code == 500 and VALUE not in response.content.decode()
    assert VALUE not in caplog.text and NOTE not in caplog.text
    assert not models.CredentialAccessLog.objects.exists()


def link_before_order(buyer, order):
    buyer.telegram_id = 88776655
    buyer.telegram_linked_at = order.created_at - timedelta(days=1)
    buyer.save(update_fields=["telegram_id", "telegram_linked_at"])


def start_recovery(order, operator, email="recovered@example.test"):
    with patch("apps.orders.proof.telegram.send_message", return_value={"message_id": 4}) as sent:
        proof().start_recovery(order_id=order.pk, actor=operator, email=email)
    assert sent.call_args.args[0] == 88776655
    text = sent.call_args.args[1]
    assert VALUE not in text and NOTE not in text
    return re.search(r"(?<!\d)\d{6}(?!\d)", text).group()


def finish_recovery(order, operator, code, email="recovered@example.test"):
    return proof().finish_recovery(order_id=order.pk, actor=operator, email=email, code=code)


def test_recovery_challenge_authorizes_one_order_address_then_requires_its_email_code(
    buyer, plan, operator, mailoutbox
):
    order, item = delivered(buyer, plan, operator)
    link_before_order(buyer, order)
    verified(order, buyer)
    original_email = order.placement_email
    code = start_recovery(order, operator)
    assert proof().email_for(order)[0] == original_email
    assert finish_recovery(order, operator, code)
    assert not finish_recovery(order, operator, code)
    order.refresh_from_db()
    assert order.placement_email == original_email
    row = models.OrderEmailRecovery.objects.get()
    assert row.operator_id == operator.pk and row.telegram_id == buyer.telegram_id
    assert row.email == "recovered@example.test" and row.created_at is not None
    with pytest.raises(PermissionDenied):
        reveal(order, item, buyer)
    verified(order, buyer)
    assert mailoutbox[-1].to == ["recovered@example.test"]
    assert reveal(order, item, buyer).lines == (LINE,)
    assert "delivery_code" in email_log.PROTECTED


@pytest.mark.parametrize("changed", ["absent", "new", "unlink", "relink", "operator", "email"])
def test_recovery_refuses_missing_new_or_changed_link_and_wrong_operator_or_address(
    buyer, plan, operator, changed
):
    order, _ = delivered(buyer, plan, operator)
    link_before_order(buyer, order)
    code = start_recovery(order, operator)
    actor, email = operator, "recovered@example.test"
    if changed == "absent":
        buyer.telegram_id = None
    elif changed == "new":
        buyer.telegram_linked_at = order.created_at + timedelta(seconds=1)
    elif changed == "unlink":
        buyer.telegram_linked_at = None
    elif changed == "relink":
        buyer.telegram_linked_at -= timedelta(seconds=1)
    elif changed == "operator":
        actor = User.objects.create_superuser("second-operator@example.test")
    else:
        email = "other@example.test"
    buyer.save(update_fields=["telegram_id", "telegram_linked_at"])
    if changed in ("absent", "new", "unlink"):
        with pytest.raises(ValidationError):
            finish_recovery(order, actor, code, email)
    else:
        assert not finish_recovery(order, actor, code, email)
    assert not models.OrderEmailRecovery.objects.exists()


def test_recovery_expiry_guess_budget_permissions_and_send_failure(buyer, plan, operator):
    order, _ = delivered(buyer, plan, operator)
    link_before_order(buyer, order)
    for actor in (buyer, staff_with("view_order", "view_orderitem")):
        with pytest.raises(PermissionDenied):
            proof().start_recovery(order_id=order.pk, actor=actor, email="recovered@example.test")
    now = timezone.now()
    with time_machine.travel(now, tick=False):
        code = start_recovery(order, operator)
        for _ in range(5):
            assert not finish_recovery(order, operator, "bad")
        assert not finish_recovery(order, operator, code)
    with time_machine.travel(now + timedelta(minutes=2), tick=False):
        code = start_recovery(order, operator)
    with time_machine.travel(now + timedelta(minutes=13), tick=False):
        assert not finish_recovery(order, operator, code)
        with patch("apps.orders.proof.telegram.send_message", side_effect=RuntimeError("failed")):
            with pytest.raises(ValidationError):
                proof().start_recovery(order_id=order.pk, actor=operator, email="new@example.test")
    assert not models.OrderEmailRecovery.objects.exists()


def test_recovery_rechecks_link_after_transport_and_command_never_prints_code(
    buyer, plan, operator, capsys
):
    order, _ = delivered(buyer, plan, operator)
    link_before_order(buyer, order)

    def changed(*args):
        User.objects.filter(pk=buyer.pk).update(telegram_linked_at=timezone.now())
        return {"message_id": 5}

    with patch("apps.orders.proof.telegram.send_message", side_effect=changed):
        with pytest.raises(ValidationError):
            proof().start_recovery(order_id=order.pk, actor=operator, email="new@example.test")
    link_before_order(buyer, order)
    code = start_recovery(order, operator)
    out = StringIO()
    with patch("apps.orders.management.commands.recover_order_email.getpass", return_value=code):
        call_command(
            "recover_order_email",
            order.order_number,
            operator_id=operator.pk,
            email="recovered@example.test",
            confirm=True,
            stdout=out,
        )
    assert code not in out.getvalue() and code not in capsys.readouterr().out
    assert models.OrderEmailRecovery.objects.count() == 1


def test_source_migration_backfills_existing_rows_and_reverses(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    services.reveal_delivery(order_id=order.pk, item_id=item.pk, actor=operator, ip=None)
    executor = MigrationExecutor(connection)
    newest = executor.loader.graph.leaf_nodes()
    previous = ("orders", "0020_input_ladder")
    try:
        executor.migrate([previous])
        executor = MigrationExecutor(connection)
        executor.migrate(newest)
        assert models.CredentialAccessLog.objects.get().source == "panel"
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM orders_credentialaccesslog")
            assert cursor.fetchone()[0] == 1
    finally:
        MigrationExecutor(connection).migrate(newest)

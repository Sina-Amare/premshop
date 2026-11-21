import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core import email_log, events
from apps.core.formatting import format_jalali, to_persian_digits
from apps.notifications import services
from apps.notifications.models import Notification
from apps.orders.tests.test_customer_claim import LABEL_A, delivered, submit
from apps.orders.tests.test_delivery import NOTE, VALUE
from apps.orders.tests.test_input_clock import event_id
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_replacement import claim, reject

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def recipient(settings):
    settings.OPERATOR_ALERT_EMAIL = "owner@example.test"


def rows():
    return Notification.objects.filter(template_name="customer_claim")


def test_committed_claim_alert_has_only_allowed_metadata(buyer, plan, operator, settings):
    from apps.orders.models import OrderItem

    order, item = delivered(buyer, plan, operator)
    OrderItem.objects.filter(pk=item.pk).update(customer_input="PRIVATE-INPUT")
    delivery = item.deliveries.latest("pk")
    delivery.note = NOTE
    delivery.save(update_fields=["note"])
    with transaction.atomic():
        submit(item, buyer)
        assert not rows().exists()
    row = rows().get()
    event = item.events.latest("pk")
    assert row.recipient == settings.OPERATOR_ALERT_EMAIL and row.channel == "email"
    panel = settings.SITE_URL.rstrip("/") + reverse("admin:orders_order_change", args=[order.pk])
    for part in (row.text_body, row.html_body):
        assert LABEL_A in part and item.product_snapshot["name"] in part
        assert to_persian_digits(order.order_number) in part
        assert format_jalali(event.created_at) in part and panel in part
    for private in (buyer.email, buyer.phone, order.placement_email, VALUE, NOTE, "PRIVATE-INPUT"):
        if private:
            assert private not in row.subject + row.text_body + row.html_body
    assert str(event.pk) in row.dedupe_key


def test_rollback_staff_claim_and_replay_cannot_create_extra_alerts(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    with pytest.raises(RuntimeError), transaction.atomic():
        submit(item, buyer)
        raise RuntimeError("rollback")
    assert not rows().exists()
    claim(item, operator)
    assert not rows().exists()
    reject(item, operator)
    expected = event_id(item)
    submit(item, buyer, expected=expected)
    submit(item, buyer, expected=expected)
    event = item.events.latest("pk")
    for _ in range(2):
        services.enqueue_event("customer_claim", event.pk, order.pk, item_id=item.pk)
        services.discover_customer_claims()
    assert rows().count() == 1


def test_discovery_recovers_missed_callback_after_rejection(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    with patch.object(events, "_consumer", side_effect=RuntimeError("missed callback")):
        submit(item, buyer)
    reject(item, operator)
    assert not rows().exists()
    assert services.discover_customer_claims() == 1
    assert services.discover_customer_claims() == 0
    assert LABEL_A in rows().get().text_body


def test_blank_operator_address_warns_and_sends_nothing(buyer, plan, operator, settings, caplog):
    settings.OPERATOR_ALERT_EMAIL = ""
    _, item = delivered(buyer, plan, operator)
    with caplog.at_level(logging.WARNING):
        submit(item, buyer)
    assert not rows().exists() and "OPERATOR_ALERT_EMAIL" in caplog.text
    assert services.discover_customer_claims() == 0


def test_alert_uses_existing_sender_and_cannot_spend_protected_reserve(
    buyer, plan, operator, settings
):
    from apps.core.models import MailAttempt

    _, item = delivered(buyer, plan, operator)
    submit(item, buyer)
    Notification.objects.exclude(template_name="customer_claim").delete()
    settings.EMAIL_QUOTA_RESET_DATE = (timezone.now().date() - timedelta(days=1)).isoformat()
    settings.EMAIL_QUOTA_STARTING_USAGE = 900
    assert "customer_claim" not in email_log.PROTECTED
    assert services.send_due_notifications() == 0
    row = rows().get()
    assert row.attempts == 0 and row.status == "pending"
    assert MailAttempt.objects.filter(template="customer_claim", status="blocked").exists()
    settings.EMAIL_QUOTA_STARTING_USAGE = 0
    from apps.notifications.models import ConfirmationSweep

    ConfirmationSweep.objects.update(quota_wait_until=None)
    Notification.objects.update(next_attempt_at=timezone.now())
    with patch("apps.notifications.services.send_templated_email", return_value=1) as send:
        assert services.send_due_notifications() == 1
    assert send.call_args.args == ("customer_claim",)
    assert send.call_args.kwargs["rendered_parts"] == (row.subject, row.text_body, row.html_body)


def test_alert_does_not_load_customer_input_or_delivery_secrets(buyer, plan, operator):
    from apps.core.fields import EncryptedTextField

    _, item = delivered(buyer, plan, operator)
    with patch.object(EncryptedTextField, "from_db_value", side_effect=AssertionError("decrypted")):
        submit(item, buyer)
        services.discover_customer_claims()
    assert rows().count() == 1

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from functools import partial
from smtplib import (
    SMTPAuthenticationError,
    SMTPDataError,
    SMTPRecipientsRefused,
    SMTPSenderRefused,
    SMTPServerDisconnected,
)
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core import events
from apps.core.formatting import format_jalali, to_persian_digits
from apps.notifications import services
from apps.notifications.models import Notification
from apps.notifications.tests.test_telegram_messages import linked
from apps.notifications.tests.test_telegram_messages import telegram as telegram
from apps.orders import services as orders
from apps.orders import status as order_status
from apps.orders.tests.test_delivery import NOTE, VALUE, deliver, queued
from apps.orders.tests.test_input_clock import act, event_id
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection
from apps.orders.tests.test_paid_cancellation import cancel
from apps.orders.tests.test_replacement import claim
from apps.payments.models import Refund
from apps.payments.services import execute_refunds, record_manual_payment

pytestmark = pytest.mark.django_db(transaction=True)


def rows(template):
    return Notification.objects.filter(template_name=template)


def test_one_payment_notice_and_one_request_per_item_after_commit(buyer, plan, operator):
    plan.telegram_required = True
    plan.save()
    order = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 2)])
    with transaction.atomic():
        payment = record_manual_payment(
            order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="PRIVATE-BANK"
        )
        assert not Notification.objects.exists()
    assert rows("payment_confirmed").count() == 1
    assert rows("input_requested").count() == 2
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="PRIVATE-BANK"
    )
    assert Notification.objects.count() == 3
    assert str(payment.pk) in rows("payment_confirmed").get().dedupe_key


def test_payment_rollback_leaves_no_notice(buyer, plan, operator):
    order = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 1)])
    with pytest.raises(RuntimeError), transaction.atomic():
        record_manual_payment(
            order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="PRIVATE-BANK"
        )
        raise RuntimeError("rollback")
    assert not Notification.objects.exists()
    assert order.items.get().status == "PENDING_PAYMENT"


def test_one_callback_failure_preserves_payment_and_other_callbacks(
    buyer, plan, operator, monkeypatch, caplog
):
    plan.telegram_required = True
    plan.save()
    order = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 1)])
    original = services.enqueue_event

    def enqueue(template, *args, **kwargs):
        if template == "payment_confirmed":
            raise ValueError("SECRET-CALLBACK-CONTENT")
        return original(template, *args, **kwargs)

    monkeypatch.setattr(events, "_consumer", enqueue)
    with caplog.at_level(logging.ERROR):
        record_manual_payment(
            order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="PRIVATE-BANK"
        )
    assert order.items.get().status == "AWAITING_INPUT"
    assert rows("input_requested").count() == 1
    assert "ValueError" in caplog.text
    assert "SECRET-CALLBACK-CONTENT" not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


def test_lifecycle_notice_never_suppresses_the_purchase_record(buyer, plan, operator):
    order, _ = queued(buyer, plan, operator)
    assert rows("payment_confirmed").exists()
    assert services.discover_confirmations() == 1
    confirmation = rows("purchase_confirmation").get()
    assert confirmation.dedupe_key == f"purchase-confirmation:{order.pk}:email"
    assert services.discover_confirmations() == 0


def test_delivery_notification_contains_no_credential_material(buyer, plan, operator, settings):
    settings.SITE_URL = "https://shop.example.test"
    order, item = queued(buyer, plan, operator)
    orders.OrderItem.objects.filter(pk=item.pk).update(customer_input="SECRET-CUSTOMER-INPUT")
    deliver(item, operator, note=NOTE)
    row = rows("delivery_completed").get()
    url = settings.SITE_URL + reverse("order-detail", args=[order.order_number])
    for part in (row.subject, row.text_body, row.html_body):
        assert VALUE not in part and NOTE not in part and "SECRET-CUSTOMER-INPUT" not in part
    assert url in row.text_body and url in row.html_body
    assert row.recipient == order.placement_email


def test_replacement_delivery_is_a_new_occurrence_and_rejection_excludes_the_note(
    buyer, plan, operator
):
    _, item = queued(buyer, plan, operator)
    deliver(item, operator)
    claim(item, operator)
    act(orders.reject_claim, item, operator, note="SECRET-REJECTION")
    rejected = rows("replacement_rejected").get()
    assert "SECRET-REJECTION" not in rejected.text_body + rejected.html_body
    claim(item, operator)
    orders.replace_delivery(
        order_id=item.order_id,
        item_id=item.pk,
        actor=operator,
        expected_event_id=event_id(item),
        lines=[orders.DeliveryLine("رمز", "SECRET-REPLACEMENT")],
        note=NOTE,
        expires_at=None,
        extra_cost=Decimal(0),
    )
    assert rows("delivery_completed").count() == 2
    assert len(set(rows("delivery_completed").values_list("dedupe_key", flat=True))) == 2
    for row in rows("delivery_completed"):
        assert "SECRET-REPLACEMENT" not in row.text_body + row.html_body


def test_delay_freezes_the_new_deadline_and_excludes_operator_notes(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    with transaction.atomic():
        moved = act(orders.delay_supply, item, operator, hours=2, note="SECRET-SUPPLIER-NOTE")
        promised = timezone.localtime(moved.due_at)
        orders.OrderItem.objects.filter(pk=item.pk).update(
            due_at=timezone.now() + timedelta(days=20)
        )
    row = rows("supply_delayed").get()
    frozen = (row.subject, row.text_body, row.html_body)
    assert "SECRET-SUPPLIER-NOTE" not in "".join(frozen)
    for part in (row.text_body, row.html_body):
        assert format_jalali(promised) in part
        assert to_persian_digits(promised.strftime("%H:%M")) in part
    services.send_due_notifications()
    sent = next(message for message in mail.outbox if message.subject == row.subject)
    assert (sent.subject, sent.body, sent.alternatives[0].content) == frozen


def test_cancelled_is_owed_and_refunded_is_only_after_execution(buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    cancel(item, operator, note="SECRET-CANCELLATION")
    cancelled = rows("item_cancelled").get()
    assert "در انتظار واریز" in cancelled.text_body
    assert "SECRET-CANCELLATION" not in cancelled.text_body + cancelled.html_body
    assert not rows("item_refunded").exists()
    refund = Refund.objects.get(order_item=item)
    execute_refunds(
        order_id=order.pk,
        refund_ids=[refund.pk],
        actor=operator,
        amount=refund.amount,
        destination_kind="card",
        destination_last4="2468",
        bank_ref="SECRET-TRANSFER-REF",
        executed_at=timezone.now(),
    )
    refunded = rows("item_refunded").get()
    assert "به شما برگرداندیم" in refunded.text_body
    for row in (cancelled, refunded):
        assert "SECRET-TRANSFER-REF" not in row.text_body + row.html_body
        assert "2468" not in row.text_body + row.html_body


@pytest.mark.parametrize("path", ["customer", "operator", "expiry"])
def test_an_unpaid_cancellation_is_one_message_for_the_whole_order(
    buyer, plan, operator, telegram, path
):
    linked(buyer)
    plan.max_quantity = 2
    plan.save()
    order = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 2)])
    if path == "customer":
        orders.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    elif path == "operator":
        orders.cancel_unpaid_order(order_id=order.pk, actor=operator, note="مشتری خواست")
    else:
        orders.Order.objects.filter(pk=order.pk).update(
            unpaid_expires_at=timezone.now() - timedelta(minutes=1)
        )
        assert orders.expire_unpaid_orders() == 1
    # One cancellation the customer lived through: one email and one bot message, not one per unit.
    email = rows("item_cancelled").get(channel="email")
    bot = rows("item_cancelled").get(channel="telegram")
    assert email.subject == f"سفارش {to_persian_digits(order.order_number)} لغو شد"
    if path == "expiry":
        said, state = "پرداختی برای این سفارش ثبت نشد.", "پایان مهلت پرداخت"
    else:
        said, state = "پرداختی برای این سفارش ثبت نشده بود.", "لغوشده"
    for body in (email.text_body, email.html_body, bot.text_body):
        assert "در انتظار واریز" not in body and "بازگشت وجه" not in body
        assert said in body and state in body and "سرویس نمونه" in body


def test_each_notice_names_its_state_in_words_and_colour(buyer, plan, operator, telegram):
    linked(buyer)
    delivered, item = queued(buyer, plan, operator)
    deliver(item, operator, note=NOTE)
    owed, owed_item = queued(buyer, plan, operator)
    cancel(owed_item, operator)
    refund = Refund.objects.get(order_item=owed_item)
    execute_refunds(
        order_id=owed.pk,
        refund_ids=[refund.pk],
        actor=operator,
        amount=refund.amount,
        destination_kind="card",
        destination_last4="6037",
        bank_ref="SECRET-TRANSFER-REF",
        executed_at=timezone.now(),
    )
    withdrawn = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 1)])
    orders.cancel_unpaid_order(order_id=withdrawn.pk, actor=buyer)
    cases = [
        ("payment_confirmed", delivered, "queued"),
        ("delivery_completed", delivered, "delivered"),
        ("item_cancelled", owed, "refund_owed"),
        ("item_refunded", owed, "refunded"),
        ("item_cancelled", withdrawn, "cancelled"),
    ]
    assert {order_status.STATES[state][1] for _, _, state in cases} == set(
        order_status.EMAIL_TONES
    ), "positive control: every tone is shown once"
    for template, order, state in cases:
        label, tone = order_status.STATES[state]
        found = rows(template).filter(order=order)
        email, bot = found.get(channel="email"), found.get(channel="telegram")
        assert f"وضعیت: {label}" in email.text_body and f"وضعیت: {label}" in bot.text_body
        background, color, border = order_status.EMAIL_TONES[tone]
        assert label in email.html_body
        assert f"background:{background}" in email.html_body
        assert f"color:{color}" in email.html_body and border in email.html_body


@pytest.mark.parametrize("historical", [False, True])
def test_event_address_is_frozen_with_an_old_order_fallback(buyer, plan, operator, historical):
    order = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 1)])
    if historical:
        orders.Order.objects.filter(pk=order.pk).update(placement_email="")
    buyer.email = "current@example.test"
    buyer.save(update_fields=["email"])
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="PRIVATE-BANK"
    )
    row = rows("payment_confirmed").get()
    expected = "current@example.test" if historical else "buyer@example.test"
    assert row.recipient == expected
    buyer.email = "later@example.test"
    buyer.save(update_fields=["email"])
    services.send_due_notifications()
    assert mail.outbox[0].to == [expected]


@pytest.mark.parametrize(
    "error,permanent",
    [
        (SMTPRecipientsRefused({"PRIVATE": (550, b"SECRET-SMTP")}), True),
        # A DATA or sender 5xx can be account-wide, such as an unverified sender domain.
        (SMTPDataError(550, b"SECRET-SMTP"), False),
        (SMTPSenderRefused(550, b"SECRET-SMTP", "PRIVATE"), False),
        (SMTPDataError(450, b"SECRET-SMTP"), False),
        (SMTPRecipientsRefused({"PRIVATE": (450, b"SECRET-SMTP")}), False),
        (SMTPServerDisconnected("SECRET-SMTP"), False),
    ],
)
def test_lifecycle_retry_classification_keeps_only_safe_error_facts(
    buyer, plan, operator, error, permanent, caplog
):
    queued(buyer, plan, operator)
    with patch("apps.notifications.services.send_templated_email", side_effect=error):
        assert services.send_due_notifications() == 0
    row = rows("payment_confirmed").get()
    assert row.attempts == 1
    assert row.status == ("failed" if permanent else "pending")
    assert (row.next_attempt_at is None) == permanent
    assert row.last_error_class == type(error).__name__
    assert "SECRET-SMTP" not in caplog.text + row.last_error_class


def test_a_rejected_smtp_login_takes_the_retry_ladder(buyer, plan, operator, monkeypatch):
    queued(buyer, plan, operator)

    def refuse(self):
        raise SMTPAuthenticationError(535, b"SECRET-SMTP")

    monkeypatch.setattr(EmailBackend, "open", refuse)
    assert services.send_due_notifications() == 0
    row = rows("payment_confirmed").get()
    assert (row.status, row.attempts, row.smtp_code) == ("pending", 1, 535)
    assert row.next_attempt_at is not None


def test_concurrent_enqueue_uses_one_occurrence(buyer, plan, operator):
    from queue import Queue

    order, item = queued(buyer, plan, operator)
    occurrence = item.events.latest("pk").pk
    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                in_connection,
                pids,
                partial(
                    services.enqueue_event,
                    "delivery_completed",
                    occurrence,
                    order.pk,
                    item_id=item.pk,
                ),
            )
            for _ in range(2)
        ]
        for future in futures:
            future.result(timeout=20)
    assert rows("delivery_completed").count() == 1


def test_no_events_are_backfilled_when_the_worker_runs(buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    Notification.objects.all().delete()
    services.discover_confirmations()
    services.send_due_notifications()
    assert Notification.objects.filter(order=order).count() == 1
    assert rows("purchase_confirmation").exists()


def test_delivery_failure_rolls_back_its_callback(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    with patch("apps.orders.models.DeliveryField.objects.bulk_create", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            deliver(item, operator)
    assert not rows("delivery_completed").exists()
    assert rows("payment_confirmed").count() == 1


def test_quota_cooldown_covers_both_message_kinds_without_spending_attempts(buyer, plan, operator):
    from apps.core.email_log import MailQuotaExceeded
    from apps.notifications.models import ConfirmationSweep

    queued(buyer, plan, operator)
    services.discover_confirmations()
    with patch("apps.notifications.services.send_templated_email", side_effect=MailQuotaExceeded):
        assert services.send_due_notifications() == 0
    assert Notification.objects.count() == 2
    assert set(Notification.objects.values_list("attempts", flat=True)) == {0}
    wait = ConfirmationSweep.objects.get().quota_wait_until
    assert set(Notification.objects.values_list("next_attempt_at", flat=True)) == {wait}


def test_every_message_projects_only_safe_facts(buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    snapshot = dict(item.product_snapshot)
    snapshot["usage_notes"] = "SECRET-PRODUCT-NOTE"
    orders.OrderItem.objects.filter(pk=item.pk).update(
        customer_input="SECRET-INPUT", product_snapshot=snapshot
    )
    for index, template in enumerate(Notification.Template.values):
        if template == "purchase_confirmation":
            continue
        row = services.enqueue_event(
            template,
            900000 + index,
            order.pk,
            item_id=None if template == "payment_confirmed" else item.pk,
            due_at=timezone.now(),
        )
        parts = row.subject + row.text_body + row.html_body
        assert "SECRET-INPUT" not in parts and "SECRET-PRODUCT-NOTE" not in parts


def test_missing_consumer_is_logged_without_breaking_the_action(
    buyer, plan, operator, monkeypatch, caplog
):
    order = orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 1)])
    monkeypatch.setattr(events, "_consumer", None)
    with caplog.at_level(logging.ERROR, logger="apps.core.events"):
        record_manual_payment(
            order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="PRIVATE-BANK"
        )
    assert order.items.get().status == "QUEUED"
    assert "RuntimeError" in caplog.text
    assert not Notification.objects.exists()

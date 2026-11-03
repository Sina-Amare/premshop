import json
import logging
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from queue import Queue
from smtplib import SMTPDataError, SMTPServerDisconnected
from threading import Event

import pytest
import time_machine
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product, ProductSection
from apps.cms.models import Page
from apps.core.models import MailAttempt
from apps.notifications import services
from apps.notifications.models import ConfirmationSweep, Notification
from apps.orders.models import Order
from apps.orders.services import OrderLine, place_order
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock

pytestmark = pytest.mark.django_db(transaction=True)


def another_order(plan, index):
    user = User.objects.create_user(f"confirmation{index}@example.test")
    return place_order(user=user, lines=[OrderLine(plan, 1)])


@pytest.mark.parametrize(
    "destination", [r"\/contact/", "&#47;contact/", "&sol;contact/", "&#x2f;contact/"]
)
def test_encoded_frozen_product_and_policy_links_are_absolute(
    buyer, plan, policies, settings, destination
):
    settings.SITE_URL = "https://shop.example.test"
    source = f"[contact]({destination})"
    Product.objects.filter(pk=plan.product_id).update(full_description=source)
    Page.objects.filter(slug="terms").update(body=source)
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    services.discover_confirmations()
    row = Notification.objects.get()
    assert row.text_body.count("https://shop.example.test/contact/") == 2
    assert row.html_body.count('href="https://shop.example.test/contact/"') == 2
    order.refresh_from_db()
    assert order.policy_snapshot["terms"]["body"] == source
    assert order.items.get().product_snapshot["full_description"] == source


def test_discovery_freezes_address_and_full_text_without_sensitive_fields(
    buyer, plan, policies, settings
):
    settings.SITE_URL = "https://shop.example.test"
    Product.objects.filter(pk=plan.product_id).update(
        full_description="Saved **Product** [contact](/contact/?a=1&b=2)",
        usage_notes="Saved usage",
        refund_note="Saved refund",
        delivery_note="Saved delivery",
    )
    ProductSection.objects.create(
        product=plan.product, kind="free", title="Saved section", body="Saved section body"
    )
    Page.objects.filter(slug="terms").update(body="Saved terms [refunds](/refunds/)")
    order = place_order(user=buyer, lines=[OrderLine(plan, 2)])
    before = deepcopy(order.policy_snapshot)
    order.items.update(customer_input="INPUT-SENTINEL", cost_snapshot=987654321)
    buyer.email = "changed@example.test"
    buyer.save(update_fields=["email"])
    Product.objects.filter(pk=plan.product_id).update(full_description="CURRENT-PRODUCT")
    Page.objects.update(body="CURRENT-POLICY")
    assert services.discover_confirmations() == 1
    row = Notification.objects.get()
    assert row.recipient == "buyer@example.test" == order.placement_email
    for part in (row.text_body, row.html_body):
        for value in (
            "Saved",
            "Product",
            "Saved usage",
            "Saved refund",
            "Saved delivery",
            "Saved section",
            "Saved section body",
            "Saved terms",
            "https://shop.example.test/refunds/",
            "https://shop.example.test" + reverse("order-detail", args=[order.order_number]),
            "پرداخت نشده",
        ):
            assert value in part
        for value in (
            "INPUT-SENTINEL",
            "987654321",
            "CURRENT-PRODUCT",
            "CURRENT-POLICY",
            "changed@example.test",
        ):
            assert value not in part
    assert "[contact](<https://shop.example.test/contact/?a=1&b=2>)" in row.text_body
    assert 'href="https://shop.example.test/contact/?a=1&amp;b=2"' in row.html_body
    assert "<details" not in row.html_body and "<script" not in row.html_body
    assert "۲" in row.text_body and "۴۰۰,۰۰۰" in row.text_body
    order.refresh_from_db()
    assert order.policy_snapshot == before
    assert order.items.first().product_snapshot["full_description"].endswith("(/contact/?a=1&b=2)")
    assert services.discover_confirmations() == 0
    services.send_due_confirmations()
    assert mail.outbox[0].to == ["buyer@example.test"]
    assert mail.outbox[0].body == row.text_body
    assert mail.outbox[0].alternatives[0].content == row.html_body
    assert MailAttempt.objects.get().status == "accepted"


def test_historical_orders_rollback_and_replay_never_create_extra_confirmations(buyer, plan):
    import uuid

    old = Order.objects.create(user=buyer, subtotal=0, total_amount=0)
    assert old.placement_email == ""
    with pytest.raises(ValueError), transaction.atomic():
        another_order(plan, 10)
        raise ValueError("rollback")
    key = uuid.uuid4()
    order = place_order(
        user=buyer, lines=[OrderLine(plan, 1)], checkout_id=key, submission_digest="a" * 64
    )
    buyer.email = "later@example.test"
    buyer.save(update_fields=["email"])
    replay = place_order(
        user=buyer, lines=[OrderLine(plan, 1)], checkout_id=key, submission_digest="a" * 64
    )
    assert replay.pk == order.pk and replay.placement_email == "buyer@example.test"
    assert services.discover_confirmations() == 1
    assert not Notification.objects.filter(order=old).exists()
    assert services.discover_confirmations() == 0


def test_one_unrenderable_order_does_not_block_discovery_or_sends(plan, monkeypatch, caplog):
    first = another_order(plan, 1)
    second = another_order(plan, 2)
    original = services.render_confirmation

    def render(order):
        if order.pk == first.pk:
            raise ValueError("SECRET-CONTENT")
        return original(order)

    monkeypatch.setattr(services, "render_confirmation", render)
    with caplog.at_level(logging.ERROR, logger="apps.notifications.services"):
        assert services.discover_confirmations() == 1
    assert services.send_due_confirmations() == 1
    assert Notification.objects.get().order_id == second.pk
    records = [r for r in caplog.records if r.name == "apps.notifications.services"]
    assert len(records) == 1
    assert str(first.pk) in records[0].getMessage() and "ValueError" in records[0].getMessage()
    assert "SECRET-CONTENT" not in caplog.text and not records[0].exc_info
    monkeypatch.setattr(services, "render_confirmation", original)
    assert services.discover_confirmations() == 1


@pytest.mark.parametrize(
    "failure", [SMTPDataError(550, b"SECRET-REPLY"), SMTPServerDisconnected("SECRET-REPLY"), "zero"]
)
def test_all_smtp_failures_take_the_six_attempt_ladder(plan, monkeypatch, failure):
    another_order(plan, 1)
    services.discover_confirmations()

    def fail(self, messages):
        if failure == "zero":
            return 0
        raise failure

    monkeypatch.setattr(EmailBackend, "send_messages", fail)
    with time_machine.travel(timezone.now(), tick=False) as clock:
        for index, delay in enumerate([60, 300, 900, 3600, 21600, None], 1):
            assert services.send_due_confirmations() == 0
            row = Notification.objects.get()
            assert row.attempts == index
            assert "SECRET-REPLY" not in json.dumps(
                {"error": row.last_error_class, "smtp": row.smtp_code}
            )
            if delay is not None:
                assert row.status == "pending"
                assert row.next_attempt_at == timezone.now() + timedelta(seconds=delay)
                clock.shift(delay - 1)
                services.send_due_confirmations()
                assert Notification.objects.get().attempts == index
                clock.shift(1)
            else:
                assert row.status == "failed" and row.next_attempt_at is None
        assert MailAttempt.objects.count() == 6


@pytest.mark.parametrize("missing_baseline", [False, True])
def test_quota_stops_tick_and_shared_cooldown_covers_arrivals_and_retries(
    plan, settings, missing_baseline
):
    if missing_baseline:
        settings.EMAIL_QUOTA_RESET_DATE = ""
    else:
        settings.EMAIL_QUOTA_STARTING_USAGE = 900
    for number in range(3):
        another_order(plan, number)
    services.discover_confirmations()
    with time_machine.travel(timezone.now(), tick=False) as clock:
        later = Notification.objects.last()
        later.next_attempt_at = timezone.now() + timedelta(minutes=1)
        later.save(update_fields=["next_attempt_at"])
        assert services.send_due_confirmations() == 0
        assert MailAttempt.objects.filter(status="blocked").count() == 1
        assert not Notification.objects.exclude(attempts=0).exists()
        wait = timezone.now() + timedelta(minutes=15)
        assert ConfirmationSweep.objects.get(pk=1).quota_wait_until == wait
        assert Notification.objects.filter(next_attempt_at=wait).count() == 2
        another_order(plan, 9)
        services.discover_confirmations()
        for _ in range(14):
            clock.shift(60)
            assert services.send_due_confirmations() == 0
        assert MailAttempt.objects.count() == 1
        settings.EMAIL_QUOTA_RESET_DATE = "2026-11-01"
        settings.EMAIL_QUOTA_STARTING_USAGE = 0
        clock.shift(60)
        assert services.send_due_confirmations() == 4
        assert len(mail.outbox) == 4
        assert not Notification.objects.exclude(status="sent", attempts=1).exists()


def test_crash_rolls_back_only_current_row_and_retry_keeps_rendered_parts(plan, monkeypatch):
    another_order(plan, 1)
    another_order(plan, 2)
    services.discover_confirmations()
    original = services.send_templated_email
    calls = 0

    def crash(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 2:
            raise SystemExit("worker stopped after SMTP acceptance")
        return result

    monkeypatch.setattr(services, "send_templated_email", crash)
    with pytest.raises(SystemExit):
        services.send_due_confirmations()
    assert Notification.objects.filter(status="sent", attempts=1).count() == 1
    assert Notification.objects.filter(status="pending", attempts=0).count() == 1
    monkeypatch.setattr(services, "send_templated_email", original)
    monkeypatch.setattr(
        "apps.core.email.render_email",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not render again")),
    )
    assert services.send_due_confirmations() == 1
    assert services.send_due_confirmations() == 0
    assert len(mail.outbox) == 3
    assert mail.outbox[1].body == mail.outbox[2].body


def test_overlapping_senders_share_quota_cooldown(plan, monkeypatch):
    from apps.core.email_log import MailQuotaExceeded

    another_order(plan, 1)
    another_order(plan, 2)
    services.discover_confirmations()
    entered, release, pids = Event(), Event(), Queue()
    calls = []

    def blocked(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        raise MailQuotaExceeded

    monkeypatch.setattr(services, "send_templated_email", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, services.send_due_confirmations)
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, services.send_due_confirmations)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert first.result(timeout=10) == second.result(timeout=10) == 0
    assert len(calls) == 1


def test_concurrent_discovery_has_one_unique_row(plan):
    another_order(plan, 1)
    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: in_connection(pids, services.discover_confirmations), range(2))
        )
    assert sum(results) == 1 and Notification.objects.count() == 1


def test_oversized_saved_conditions_are_complete_and_snapshot_links_do_not_change(
    buyer, plan, policies
):
    body = "متن کامل شرایط خرید. " * 8000 + "END-SAVED-TEXT"
    Page.objects.filter(slug="terms").update(body=body)
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    services.discover_confirmations()
    row = Notification.objects.get()
    assert len(row.html_body.encode()) > 102 * 1024
    assert body in row.text_body and "END-SAVED-TEXT" in row.html_body
    order.refresh_from_db()
    assert order.policy_snapshot["terms"]["body"] == body


def test_successful_overlapping_senders_send_one_order_once(plan, monkeypatch):
    another_order(plan, 1)
    services.discover_confirmations()
    entered, release, pids = Event(), Event(), Queue()
    original = services.send_templated_email

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(services, "send_templated_email", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, services.send_due_confirmations)
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, services.send_due_confirmations)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert first.result(timeout=10) + second.result(timeout=10) == 1
    assert len(mail.outbox) == MailAttempt.objects.count() == 1


def test_uncertain_failure_then_success_reuses_content_and_clears_error(plan, monkeypatch):
    another_order(plan, 1)
    services.discover_confirmations()
    saved = Notification.objects.get()
    original = EmailBackend.send_messages

    def fail(*args, **kwargs):
        raise SMTPServerDisconnected("private reply")

    monkeypatch.setattr(EmailBackend, "send_messages", fail)
    with time_machine.travel(timezone.now(), tick=False) as clock:
        services.send_due_confirmations()
        assert MailAttempt.objects.get().status == "uncertain"
        clock.shift(60)
        monkeypatch.setattr(EmailBackend, "send_messages", original)
        assert services.send_due_confirmations() == 1
    row = Notification.objects.get()
    assert row.attempts == 2 and row.status == "sent" and row.last_error_class == ""
    assert mail.outbox[0].body == saved.text_body


def test_task_is_periodic_and_missed_ticks_catch_up(plan):
    from huey.contrib.djhuey import HUEY

    from apps.notifications.tasks import confirm_new_orders

    another_order(plan, 1)
    confirm_new_orders()
    assert not Notification.objects.exists() and not mail.outbox
    HUEY.execute(HUEY.dequeue())
    assert Notification.objects.get().status == "sent"
    confirm_new_orders()
    HUEY.execute(HUEY.dequeue())
    assert len(mail.outbox) == 1


def test_panel_is_read_only_and_shows_waiting_and_failed(client, plan):
    another_order(plan, 1)
    another_order(plan, 2)
    services.discover_confirmations()
    row = Notification.objects.last()
    Notification.objects.filter(pk=row.pk).update(status="failed", next_attempt_at=None)
    url = reverse("admin:notifications_notification_changelist")
    staff = User.objects.create_user("staff@example.test", is_staff=True)
    client.force_login(staff)
    assert client.get(url).status_code == 403
    staff.is_superuser = True
    staff.save(update_fields=["is_superuser"])
    response = client.get(url)
    assert response.status_code == 200
    assert "در انتظار" in response.content.decode() and "ناموفق" in response.content.decode()
    assert (
        client.post(
            reverse("admin:notifications_notification_change", args=[row.pk]),
            {"recipient": "attacker@example.test"},
        ).status_code
        == 403
    )
    assert client.get(reverse("admin:notifications_notification_add")).status_code == 403

from datetime import UTC, datetime, timedelta
from smtplib import (
    SMTPDataError,
    SMTPHeloError,
    SMTPNotSupportedError,
    SMTPRecipientsRefused,
    SMTPServerDisconnected,
)

import pytest
from django.core import mail
from django.db import transaction
from django.urls import reverse

from apps.core.email import send_templated_email
from apps.core.views import EMAIL_PREVIEWS

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def quota(settings):
    settings.EMAIL_QUOTA_RESET_DATE = datetime.now(UTC).date().replace(day=1).isoformat()
    settings.EMAIL_QUOTA_STARTING_USAGE = 0


def send(name="otp_code"):
    return send_templated_email(name, to=["buyer@example.test"], context=EMAIL_PREVIEWS[name])


@pytest.mark.parametrize("name", ["otp_code", "password_reset", "signin_alert", "password_changed"])
def test_security_mail_can_spend_the_reserve(name, settings):
    settings.EMAIL_QUOTA_STARTING_USAGE = 999
    assert send(name) == 1


def test_lower_priority_mail_cannot_spend_the_reserve(settings):
    from apps.core.email_log import MailQuotaExceeded
    from apps.core.models import MailAttempt

    settings.EMAIL_QUOTA_STARTING_USAGE = 900
    with pytest.raises(MailQuotaExceeded):
        send("item_delivered")
    assert not mail.outbox
    assert MailAttempt.objects.get().status == "blocked"


def test_only_counted_attempts_spend_daily_capacity():
    from apps.core.email_log import MailQuotaExceeded
    from apps.core.models import MailAttempt

    MailAttempt.objects.bulk_create(
        [
            MailAttempt(template="otp_code", recipient_hash="a" * 64, status="accepted")
            for _ in range(200)
        ]
    )
    with pytest.raises(MailQuotaExceeded):
        send()
    MailAttempt.objects.filter(status="accepted").update(status="rejected")
    assert send() == 1


def test_recovery_rollback_keeps_the_accepted_attempt():
    from apps.core.models import MailAttempt

    with pytest.raises(ValueError), transaction.atomic():
        assert send("password_reset") == 1
        assert MailAttempt.objects.get().status == "accepted"
        raise ValueError("rollback")
    assert MailAttempt.objects.get().status == "accepted"


@pytest.mark.parametrize(
    "error,status",
    [
        (SMTPDataError(451, b"temporary rejection"), "rejected"),
        (SMTPHeloError(451, b"temporary greeting rejection"), "rejected"),
        (SMTPRecipientsRefused({"buyer@example.test": (550, b"refused")}), "rejected"),
        (SMTPNotSupportedError("no SMTPUTF8"), "rejected"),
        (SMTPServerDisconnected("lost result"), "uncertain"),
    ],
)
def test_transport_failures_survive_the_callers_rollback(monkeypatch, error, status):
    from django.core.mail.backends.locmem import EmailBackend

    from apps.core.models import MailAttempt

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(EmailBackend, "send_messages", fail)
    with pytest.raises(type(error)), transaction.atomic():
        send("password_reset")
    assert MailAttempt.objects.get().status == status


def test_connection_failure_does_not_spend_capacity(monkeypatch):
    from django.core.mail.backends.locmem import EmailBackend

    from apps.core.models import MailAttempt

    def fail(*args, **kwargs):
        raise OSError("connection failed")

    monkeypatch.setattr(EmailBackend, "open", fail)
    with pytest.raises(OSError):
        send()
    assert MailAttempt.objects.get().status == "rejected"


@pytest.mark.parametrize("stage", ["reserve", "finish"])
def test_bookkeeping_failure_never_prevents_a_code_or_changes_its_success(monkeypatch, stage):
    from apps.core import email_log

    def fail(*args, **kwargs):
        raise RuntimeError("bookkeeping failed")

    monkeypatch.setattr(email_log, stage, fail)
    with transaction.atomic():
        assert send() == 1
        assert not transaction.get_rollback()
    assert len(mail.outbox) == 1


def test_log_contains_no_address_code_link_or_context():
    from apps.core.models import MailAttempt

    send()
    saved = str(MailAttempt.objects.values().get())
    assert "buyer@example.test" not in saved
    assert "418305" not in saved
    assert "http" not in saved


def test_attempt_cleanup_keeps_the_last_90_days():
    from apps.core.models import MailAttempt
    from apps.core.tasks import clean_mail_attempts

    send()
    row = MailAttempt.objects.get()
    MailAttempt.objects.filter(pk=row.pk).update(created_at=datetime.now(UTC) - timedelta(days=91))
    assert clean_mail_attempts.call_local() == 1
    assert not MailAttempt.objects.exists()


def test_panel_search_is_exact_and_read_only(client, django_user_model):
    from apps.core.models import MailAttempt

    user = django_user_model.objects.create_superuser("owner@example.test", "owner-password")
    send()
    client.force_login(user)
    url = reverse("admin:core_mailattempt_changelist")
    response = client.post(url, {"recipient": " BUYER@EXAMPLE.TEST "})
    assert "buyer" not in response.url
    assert client.get(response.url).context["cl"].result_count == 1
    assert client.get(url, {"q": "buyer"}).context["cl"].result_count == 0
    row = MailAttempt.objects.get()
    assert client.post(reverse("admin:core_mailattempt_add"), {}).status_code == 403
    assert (
        client.post(reverse("admin:core_mailattempt_change", args=[row.pk]), {}).status_code == 403
    )
    assert (
        client.post(reverse("admin:core_mailattempt_delete", args=[row.pk]), {}).status_code == 403
    )


@pytest.mark.parametrize("status", ["in_flight", "accepted", "uncertain"])
def test_each_counted_status_blocks_at_the_daily_limit(status):
    from apps.core.email_log import MailQuotaExceeded
    from apps.core.models import MailAttempt

    MailAttempt.objects.bulk_create(
        [
            MailAttempt(template="otp_code", recipient_hash="b" * 64, status=status)
            for _ in range(200)
        ]
    )
    with pytest.raises(MailQuotaExceeded):
        send()


def test_daily_reserve_and_exact_24_hour_expiry():
    import time_machine

    from apps.core.email_log import MailQuotaExceeded
    from apps.core.models import MailAttempt

    at = datetime.now(UTC)
    with time_machine.travel(at, tick=False):
        MailAttempt.objects.bulk_create(
            [
                MailAttempt(template="otp_code", recipient_hash="c" * 64, status="accepted")
                for _ in range(180)
            ]
        )
        with pytest.raises(MailQuotaExceeded):
            send("item_delivered")
        assert send("signin_alert") == 1
    with time_machine.travel(at + timedelta(hours=24), tick=False):
        assert send("item_delivered") == 1


def test_the_baseline_expires_at_the_next_actual_monthly_boundary(settings):
    import time_machine

    from apps.core.email_log import MailQuotaExceeded

    settings.EMAIL_QUOTA_RESET_DATE = "2026-01-31"
    settings.EMAIL_QUOTA_STARTING_USAGE = 1000
    with time_machine.travel("2026-02-27T23:59:59Z", tick=False), pytest.raises(MailQuotaExceeded):
        send()
    with time_machine.travel("2026-02-28T00:00:00Z", tick=False):
        assert send() == 1


def test_multiple_recipients_use_multiple_units(settings):
    from apps.core.email_log import MailQuotaExceeded
    from apps.core.models import MailAttempt

    settings.EMAIL_QUOTA_STARTING_USAGE = 999
    with pytest.raises(MailQuotaExceeded):
        send_templated_email(
            "otp_code", to=["a@example.test", "b@example.test"], context=EMAIL_PREVIEWS["otp_code"]
        )
    assert list(MailAttempt.objects.values_list("status", flat=True)) == ["blocked", "blocked"]
    assert not mail.outbox


def test_another_send_can_finish_while_the_first_transport_waits(monkeypatch, settings):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.core.mail.backends.locmem import EmailBackend

    from apps.core.email_log import MailQuotaExceeded
    from apps.core.models import MailAttempt

    entered, release = Event(), Event()
    original = EmailBackend.send_messages

    def wait(backend, messages):
        if messages[0].to == ["first@example.test"]:
            entered.set()
            assert release.wait(10)
        return original(backend, messages)

    monkeypatch.setattr(EmailBackend, "send_messages", wait)
    settings.EMAIL_QUOTA_STARTING_USAGE = 998
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            send_templated_email,
            "otp_code",
            to=["first@example.test"],
            context=EMAIL_PREVIEWS["otp_code"],
        )
        try:
            assert entered.wait(5)
            assert MailAttempt.objects.get().status == "in_flight"
            assert send() == 1
            with pytest.raises(MailQuotaExceeded):
                send()
        finally:
            release.set()
        assert pending.result(timeout=5) == 1


@pytest.mark.parametrize("json", [False, True])
def test_code_request_returns_temporary_unavailability_at_a_known_limit(client, settings, json):
    from apps.accounts.views import PENDING_EMAIL_KEY

    settings.EMAIL_QUOTA_STARTING_USAGE = 1000
    response = client.post(
        reverse("login-code"),
        {"email": "buyer@example.test"},
        **({"HTTP_ACCEPT": "application/json"} if json else {}),
    )
    assert response.status_code == 503
    assert PENDING_EMAIL_KEY not in client.session
    assert not mail.outbox


def test_recovery_failure_is_logged_despite_the_real_session_transaction(
    client, django_user_model, monkeypatch
):
    from django.core.mail.backends.locmem import EmailBackend

    from apps.core.models import MailAttempt

    user = django_user_model.objects.create_user("recovery@example.test", "owner-password")
    client.force_login(user)

    def fail(*args, **kwargs):
        raise SMTPServerDisconnected("uncertain")

    monkeypatch.setattr(EmailBackend, "send_messages", fail)
    with pytest.raises(SMTPServerDisconnected):
        client.post(reverse("account-password-recovery"), {"action": "request"})
    assert MailAttempt.objects.get().status == "uncertain"


def test_missing_quota_configuration_still_records_security_mail(settings):
    from apps.core.models import MailAttempt

    settings.EMAIL_QUOTA_RESET_DATE = ""
    assert send() == 1
    assert MailAttempt.objects.get().status == "accepted"


@pytest.mark.parametrize("value", ["", "unknown", "-1", "1001"])
def test_invalid_starting_usage_never_prevents_a_security_send(settings, value):
    from apps.core.models import MailAttempt

    settings.EMAIL_QUOTA_STARTING_USAGE = value
    assert send() == 1
    assert MailAttempt.objects.get().status == "accepted"


def test_panel_list_does_not_expose_attempts_to_customers_or_unprivileged_staff(
    client, django_user_model
):
    user = django_user_model.objects.create_user("customer@example.test", "owner-password")
    url = reverse("admin:core_mailattempt_changelist")
    assert client.get(url).status_code == 302
    client.force_login(user)
    assert client.get(url).status_code == 302
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    assert client.get(url).status_code == 403
    assert client.post(url, {"recipient": "buyer@example.test"}).status_code == 403


def test_resend_and_recovery_report_a_known_quota_limit(client, django_user_model, settings):
    from apps.accounts.views import PENDING_EMAIL_KEY

    settings.EMAIL_QUOTA_STARTING_USAGE = 1000
    session = client.session
    session[PENDING_EMAIL_KEY] = "buyer@example.test"
    session.save()
    response = client.post(reverse("login-code-resend"), HTTP_ACCEPT="application/json")
    assert response.status_code == 503
    assert response.json()["ok"] is False
    user = django_user_model.objects.create_user("recovery@example.test", "owner-password")
    client.force_login(user)
    response = client.post(reverse("account-password-recovery"), {"action": "request"})
    assert response.status_code == 503
    assert "کد جدید را به ایمیلتان فرستادیم" not in response.content.decode()
    assert not mail.outbox


def test_an_exhausted_security_notice_cannot_undo_code_signin(client, django_user_model, settings):
    import re

    from apps.accounts.otp import PERSIAN_TO_ASCII_DIGITS

    user = django_user_model.objects.create_user("signin@example.test", "owner-password")
    settings.EMAIL_QUOTA_STARTING_USAGE = 999
    assert client.post(reverse("login-code"), {"email": user.email}).status_code == 302
    code = re.search(
        r"\b[0-9]{6}\b", mail.outbox[0].body.translate(PERSIAN_TO_ASCII_DIGITS)
    ).group()
    assert client.post(reverse("login-code-verify"), {"code": code}).status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
    assert len(mail.outbox) == 1

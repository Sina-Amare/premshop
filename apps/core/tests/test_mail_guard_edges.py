"""The guard's windows, refusals and privacy promises."""

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from smtplib import SMTPDataError, SMTPServerDisconnected

import pytest
import time_machine
from django.contrib.messages import get_messages
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.urls import reverse

from apps.core import email_log
from apps.core.email import send_templated_email
from apps.core.email_log import MailQuotaExceeded, fingerprint
from apps.core.models import MailAttempt
from apps.core.tasks import clean_mail_attempts
from apps.core.views import EMAIL_PREVIEWS

pytestmark = pytest.mark.django_db(transaction=True)
THIS_MONTH = datetime.now(UTC).date().replace(day=1).isoformat()


@pytest.fixture(autouse=True)
def quota(settings):
    settings.EMAIL_QUOTA_RESET_DATE = THIS_MONTH
    settings.EMAIL_QUOTA_STARTING_USAGE = 0


def send(name="otp_code"):
    return send_templated_email(name, to=["buyer@example.test"], context=EMAIL_PREVIEWS[name])


def attempts(count, **fields):
    MailAttempt.objects.bulk_create(
        [
            MailAttempt(**{"template": "otp_code", "recipient_hash": "d" * 64, **fields})
            for _ in range(count)
        ]
    )


def test_security_mail_may_use_exactly_the_whole_daily_allowance():
    attempts(199, status="accepted")
    assert send() == 1
    with pytest.raises(MailQuotaExceeded):
        send()


def test_refused_attempts_do_not_spend_capacity():
    attempts(200, status="blocked")
    assert send() == 1


def test_the_month_counts_older_rows_and_forgets_the_last_period(settings):
    settings.EMAIL_QUOTA_RESET_DATE = "2026-01-15"
    attempts(1000, status="accepted", created_at=datetime(2026, 2, 16, tzinfo=UTC))
    with time_machine.travel("2026-02-20T12:00:00Z", tick=False), pytest.raises(MailQuotaExceeded):
        send()
    with time_machine.travel("2026-03-15T00:00:00Z", tick=False):
        assert send() == 1


@pytest.mark.parametrize(
    "reset,usage",
    [
        ("", "0"),
        ("not-a-date", "0"),
        # The dashboard shows the next reset; the setting wants the last one.
        ("2999-01-01", "0"),
        (THIS_MONTH, "unknown"),
        (THIS_MONTH, "-1"),
        (THIS_MONTH, "1001"),
    ],
)
def test_other_mail_waits_when_the_baseline_cannot_be_trusted(settings, reset, usage):
    settings.EMAIL_QUOTA_RESET_DATE = reset
    settings.EMAIL_QUOTA_STARTING_USAGE = usage
    with pytest.raises(MailQuotaExceeded):
        send("item_delivered")
    assert not mail.outbox
    assert MailAttempt.objects.get().status == "blocked"


def test_other_mail_waits_when_bookkeeping_is_down(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("bookkeeping failed")

    monkeypatch.setattr(email_log, "reserve", fail)
    with pytest.raises(MailQuotaExceeded):
        send("item_delivered")
    assert not mail.outbox


def test_bookkeeping_waits_are_bounded():
    with email_log._cursor() as cursor:
        cursor.execute("SHOW statement_timeout")
        assert cursor.fetchone()[0] == "2s"
        assert cursor.connection.info.get_parameters()["connect_timeout"] == "3"


def test_a_failed_goodbye_cannot_fail_an_accepted_send(monkeypatch):
    def fail(self):
        raise SMTPServerDisconnected("gone")

    monkeypatch.setattr(EmailBackend, "close", fail)
    assert send() == 1
    assert MailAttempt.objects.get().status == "accepted"


def test_a_message_the_backend_did_not_take_does_not_count(monkeypatch):
    monkeypatch.setattr(EmailBackend, "send_messages", lambda *args: 0)
    assert send() == 0
    assert MailAttempt.objects.get().status == "rejected"


def test_the_relays_reply_text_stays_out_of_the_record(monkeypatch):
    def fail(*args):
        raise SMTPDataError(451, b"refused buyer@example.test")

    monkeypatch.setattr(EmailBackend, "send_messages", fail)
    with pytest.raises(SMTPDataError):
        send()
    row = MailAttempt.objects.get()
    assert (row.error_type, row.smtp_code) == ("SMTPDataError", 451)


def test_a_bookkeeping_error_logs_only_its_class(caplog):
    with caplog.at_level(logging.ERROR, logger="apps.core.email_log"):
        email_log.accounting_error(RuntimeError("buyer@example.test"))
    assert "RuntimeError" in caplog.text
    assert "buyer@example.test" not in caplog.text


def test_the_recipient_fingerprint_is_keyed_and_spelling_blind(settings):
    first = fingerprint(" Buyer@Example.Test ")
    assert first == fingerprint("buyer@example.test")
    assert first != hashlib.sha256(b"buyer@example.test").hexdigest()
    settings.SECRET_KEY += "-rotated"
    assert fingerprint("buyer@example.test") != first


def test_cleanup_keeps_what_is_younger_than_90_days():
    now = datetime.now(UTC)
    attempts(1, status="accepted", created_at=now - timedelta(days=91))
    attempts(1, status="accepted", created_at=now - timedelta(days=89))
    assert clean_mail_attempts.call_local() == 1
    assert MailAttempt.objects.count() == 1


def test_panel_search_needs_the_whole_address(client, django_user_model):
    client.force_login(django_user_model.objects.create_superuser("owner@example.test"))
    send()
    url = reverse("admin:core_mailattempt_changelist")
    whole = fingerprint("buyer@example.test")
    assert client.get(url, {"q": whole}).context["cl"].result_count == 1
    assert client.get(url, {"q": whole[:20]}).context["cl"].result_count == 0
    response = client.post(url, {"recipient": "buyer"})
    assert response.url == url
    assert "کامل" in str(next(iter(get_messages(response.wsgi_request))))


@pytest.mark.parametrize(
    "rows,usage,warning,reserve,exhausted,line",
    [
        (159, 0, False, False, False, ""),
        (0, 799, False, False, False, ""),
        (160, 0, True, False, False, ""),
        (0, 800, True, False, False, ""),
        (180, 0, True, True, False, "نگه داشته شده"),
        (0, 900, True, True, False, "نگه داشته شده"),
        (200, 0, True, True, True, "سقف ارسال پر شده"),
        (0, 1000, True, True, True, "سقف ارسال پر شده"),
    ],
)
def test_the_banner_follows_each_limit(
    client, django_user_model, settings, rows, usage, warning, reserve, exhausted, line
):
    settings.EMAIL_QUOTA_STARTING_USAGE = usage
    attempts(rows, status="accepted")
    client.force_login(django_user_model.objects.create_superuser("owner@example.test"))
    response = client.get(reverse("admin:index"))
    quota = response.context["mail_quota"]
    assert (quota["warning"], quota["reserve"], quota["exhausted"]) == (warning, reserve, exhausted)
    assert quota["daily_remaining"] == 200 - rows
    assert quota["monthly_remaining"] == 1000 - rows - usage
    body = response.content.decode()
    assert ("data-mail-quota" in body) == warning
    assert line in body


def test_the_banner_explains_an_accounting_outage_to_staff_only(
    client, django_user_model, settings
):
    settings.EMAIL_QUOTA_RESET_DATE = ""
    customer = django_user_model.objects.create_user("customer@example.test")
    client.force_login(customer)
    assert "data-mail-quota" not in client.get(reverse("admin:login")).content.decode()
    client.force_login(django_user_model.objects.create_superuser("owner@example.test"))
    assert "data-mail-quota" in client.get(reverse("admin:index")).content.decode()

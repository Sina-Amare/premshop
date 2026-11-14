"""Telegram code requests disclose no account lookup and cannot replace newer work."""

from __future__ import annotations

import re

import pytest
import time_machine
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.accounts import otp, tasks
from apps.accounts.models import User
from apps.core import ratelimit, sms
from apps.core import telegram as api

pytestmark = pytest.mark.django_db
PHONE = "09123456789"
JSON = {"HTTP_ACCEPT": "application/json"}
REPLY = "اگر حساب تلگرامتان به حساب کاربری متصل باشد، کد ورود را در ربات پرم‌شاپ می‌بینید."


@pytest.fixture
def linked():
    return User.objects.create_user(
        phone=PHONE, telegram_id=12345, telegram_linked_at=timezone.now(), is_verified=True
    )


@pytest.fixture
def queued(monkeypatch, settings):
    settings.TELEGRAM_BOT_TOKEN = "test-bot-token"  # noqa: S105 - a fake token, never sent
    settings.TELEGRAM_BOT_USERNAME = "premshop_test_bot"
    jobs = []
    monkeypatch.setattr(tasks, "send_telegram_code", lambda *args: jobs.append(args), raising=False)
    return jobs


@pytest.fixture
def sent(monkeypatch):
    messages = []

    def send(chat_id, text, **kwargs):
        messages.append((chat_id, text, kwargs))
        return {"message_id": len(messages)}

    monkeypatch.setattr(api, "send_message", send)
    return messages


def ask(client, channel="telegram", **extra):
    return client.post(reverse("login-code"), {"phone": PHONE, "channel": channel, **extra}, **JSON)


def deliver(job):
    from apps.accounts.telegram_codes import deliver_code

    deliver_code(*job)


def code_from(messages):
    found = re.search(r"\d{6}", messages[-1][1])
    assert found
    return found.group()


def test_both_entry_points_offer_sms_by_default_and_telegram(client):
    for url in [reverse("login-code"), reverse("checkout")]:
        body = client.get(url).content.decode()
        assert 'name="channel"' in body and 'value="telegram"' in body
        assert re.search(r'value="sms"[^>]*checked', body)
        assert "روش دریافت کد" in body


@pytest.mark.parametrize("state", ["unknown", "unlinked", "linked", "inactive", "staff"])
def test_all_numbers_get_the_same_acknowledgement_and_a_queued_job(client, queued, sent, state):
    if state != "unknown":
        User.objects.create_user(
            phone=PHONE,
            telegram_id=None if state == "unlinked" else 12345,
            telegram_linked_at=timezone.now(),
            is_active=state != "inactive",
            is_staff=state == "staff",
        )
    with time_machine.travel("2026-11-14 09:00:00", tick=False):
        response = ask(client)
        assert response.json() == {
            "ok": True,
            "expires_in": 600,
            "cooldown": 120,
            "channel": "telegram",
        }
        assert len(queued) == 1 and not sent and not sms.outbox
        deliver(queued[0])
    assert bool(sent) == (state == "linked")
    assert User.objects.count() == (state != "unknown")


def test_code_page_has_the_conditional_reply_bot_link_and_immediate_sms_form(client, queued):
    ask(client)
    body = client.get(reverse("login-code-verify")).content.decode()
    assert 'href="https://t.me/premshop_test_bot"' in body
    assert (
        REPLY.replace("ربات پرم‌شاپ", '<a href="https://t.me/premshop_test_bot">ربات پرم‌شاپ</a>')
        in body
    )
    assert 'id="sms-form"' in body and "دریافت کد با پیامک" in body
    assert "hidden" not in re.search(r'<p[^>]*id="delivery-telegram"[^>]*>', body).group()


def test_invalid_channel_is_refused_before_sending_or_queuing(client, queued):
    response = ask(client, "email")
    assert response.status_code == 400 and not response.json()["ok"]
    assert not queued and not sms.outbox


def test_telegram_uses_no_sms_allowance_and_sms_is_available_immediately(client, linked, queued):
    ask(client)
    assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_PHONE_COOLDOWN, PHONE) == 1
    assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, PHONE) == 10
    response = client.post(reverse("login-code-resend"), {"channel": "sms"}, **JSON)
    assert response.json()["channel"] == "sms" and len(sms.outbox) == 1


def test_exhausted_sms_shop_budget_does_not_block_telegram(client, linked, queued):
    for _ in range(ratelimit.SMS_DAILY_BUDGET.times):
        ratelimit.hit(ratelimit.SMS_DAILY_BUDGET, "shop")
    assert ask(client).json()["ok"]
    assert len(queued) == 1


def test_a_second_telegram_request_has_its_own_cooldown(client, queued):
    assert ask(client).json()["ok"]
    assert ask(client).status_code == 429
    assert len(queued) == 1 and not sms.outbox


def test_plain_resend_keeps_telegram_and_never_claims_delivery(client, queued):
    with time_machine.travel("2026-11-14 09:00:00", tick=False) as clock:
        ask(client)
        clock.shift(121)
        response = client.post(reverse("login-code-resend"), follow=True)
    assert len(queued) == 2 and not sms.outbox
    assert "کد جدید را فرستادیم." not in response.content.decode()


def test_worker_protects_the_message_and_stores_only_the_hash(client, linked, queued, sent):
    ask(client)
    deliver(queued[0])
    code = code_from(sent)
    assert sent[0][0] == linked.telegram_id and sent[0][2]["protect_content"] is True
    assert sent[0][1] == f"کد ورود به پرم‌شاپ: {code}\nاین کد را به کسی ندهید."
    issuance = cache.get(otp._key(PHONE))
    record = cache.get(otp._record_key(PHONE, issuance))
    assert record["digest"] == otp._digest(code) and "code" not in record
    assert len(queued[0]) == 4 and len(queued[0][1]) >= 22


def test_queueing_alone_and_a_failed_send_preserve_the_live_sms(
    client, linked, queued, monkeypatch
):
    issued = otp.send_login_code(PHONE)
    ask(client)
    monkeypatch.setattr(
        api, "send_message", lambda *a, **kw: (_ for _ in ()).throw(api.TelegramError(403))
    )
    deliver(queued[0])
    assert otp.verify(PHONE, issued.code)


def test_an_sms_replacement_cancels_an_older_job(client, linked, queued, sent):
    ask(client)
    client.post(reverse("login-code-resend"), {"channel": "sms"}, **JSON)
    deliver(queued[0])
    assert not sent
    assert otp.verify(PHONE, code_from(sms.outbox))


def test_sms_accepted_during_telegram_send_cannot_be_overwritten(
    client, linked, queued, sent, monkeypatch
):
    ask(client)
    send = api.send_message

    def delayed(*args, **kwargs):
        response = client.post(reverse("login-code-resend"), {"channel": "sms"}, **JSON)
        assert response.json()["ok"]
        return send(*args, **kwargs)

    monkeypatch.setattr(api, "send_message", delayed)
    deliver(queued[0])
    assert otp.verify(PHONE, code_from(sms.outbox))
    assert not otp.verify(PHONE, code_from(sent))


@pytest.mark.parametrize("when", ["before", "during", "after"])
def test_changing_the_link_invalidates_the_request_and_the_code(
    client, linked, queued, sent, monkeypatch, when
):
    ask(client)

    def change():
        User.objects.filter(pk=linked.pk).update(telegram_linked_at=timezone.now())

    if when == "before":
        change()
    elif when == "during":
        send = api.send_message

        def relinking(*args, **kwargs):
            change()
            return send(*args, **kwargs)

        monkeypatch.setattr(api, "send_message", relinking)
    deliver(queued[0])
    if when == "before":
        assert not sent
    elif when == "during":
        assert cache.get(otp._key(PHONE)) is None
    else:
        change()
        response = client.post(reverse("login-code-verify"), {"code": code_from(sent)}, **JSON)
        assert not response.json()["ok"] and "_auth_user_id" not in client.session


def test_an_expired_job_and_a_duplicate_job_send_nothing_extra(client, linked, queued, sent):
    with time_machine.travel("2026-11-14 09:00:00", tick=False) as clock:
        ask(client)
        deliver(queued[0])
        deliver(queued[0])
        assert len(sent) == 1
        clock.shift(601)
        deliver(queued[0])
        assert len(sent) == 1


def test_queue_failure_preserves_the_live_code_and_session(client, linked, queued, monkeypatch):
    ask(client, "sms")
    expiry = client.session["accounts.pending_expires_at"]

    def fail(*args):
        raise ConnectionError("queue unavailable")

    monkeypatch.setattr(tasks, "send_telegram_code", fail)
    assert ask(client).status_code == 503
    assert client.session["accounts.pending_expires_at"] == expiry
    assert otp.verify(PHONE, code_from(sms.outbox))


def test_successful_sign_in_only_queues_message_deletion(
    client, linked, queued, sent, monkeypatch, django_capture_on_commit_callbacks
):
    ask(client)
    deliver(queued[0])
    deletions = []
    monkeypatch.setattr(
        tasks, "delete_login_message", lambda *args: deletions.append(args), raising=False
    )
    monkeypatch.setattr(
        api, "delete_message", lambda *args: pytest.fail("Telegram called inside sign-in")
    )
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("login-code-verify"), {"code": code_from(sent)}, **JSON)
    assert response.json()["ok"] and deletions == [(12345, 1)]
    assert client.session["_auth_user_id"] == str(linked.pk)


def test_verifying_a_live_sms_cancels_pending_telegram(client, linked, queued, sent):
    ask(client, "sms")
    code = code_from(sms.outbox)
    ask(client)
    response = client.post(reverse("login-code-verify"), {"code": code}, **JSON)
    assert response.json()["ok"]
    deliver(queued[0])
    assert not sent


def test_an_undelivered_job_expires_without_sending(client, linked, queued, sent):
    with time_machine.travel("2026-11-14 09:00:00", tick=False) as clock:
        ask(client)
        clock.shift(600)
        deliver(queued[0])
    assert not sent


def test_queue_waiting_does_not_extend_the_code_deadline(client, linked, queued, sent):
    with time_machine.travel("2026-11-14 09:00:00", tick=False) as clock:
        ask(client)
        clock.shift(599)
        deliver(queued[0])
        clock.shift(1)
        response = client.post(reverse("login-code-verify"), {"code": code_from(sent)}, **JSON)
    assert not response.json()["ok"]


def test_the_sixth_telegram_request_in_an_hour_is_refused(client, queued):
    with time_machine.travel("2026-11-14 09:00:00", tick=False) as clock:
        for _ in range(5):
            assert ask(client).json()["ok"]
            clock.shift(121)
        assert ask(client).status_code == 429
    assert len(queued) == 5 and not sms.outbox


def test_the_eleventh_telegram_request_from_a_network_is_refused(client, queued):
    for index in range(11):
        response = client.post(
            reverse("login-code"), {"phone": f"0912000{index:04d}", "channel": "telegram"}, **JSON
        )
        assert response.status_code == (200 if index < 10 else 429)
    assert len(queued) == 10


def test_switching_channels_does_not_buy_more_guesses(client, linked, queued, sent):
    with time_machine.travel("2026-11-14 09:00:00", tick=False) as clock:
        ask(client)
        deliver(queued[0])
        for channel in ["telegram", "sms"]:
            if channel == "sms":
                client.post(reverse("login-code-resend"), {"channel": "sms"}, **JSON)
            code = code_from(sent if channel == "telegram" else sms.outbox)
            wrong = f"{(int(code) + 1) % 10**6:06d}"
            for _ in range(5):
                assert not client.post(
                    reverse("login-code-verify"), {"code": wrong}, **JSON
                ).json()["ok"]
        clock.shift(121)
        ask(client)
        deliver(queued[-1])
        response = client.post(reverse("login-code-verify"), {"code": code_from(sent)}, **JSON)
    assert response.status_code == 429 and "_auth_user_id" not in client.session


@pytest.mark.parametrize("status", [None, 403, 429, 500])
def test_a_transport_failure_is_private_and_does_not_activate_a_code(
    client, linked, queued, monkeypatch, caplog, status
):
    def refuse(*args, **kwargs):
        raise api.TelegramError(status)

    monkeypatch.setattr(api, "send_message", refuse)
    response = ask(client)
    deliver(queued[0])
    assert response.status_code == 200 and response.json()["ok"]
    assert cache.get(otp._key(PHONE)) is None
    assert PHONE not in caplog.text and not sms.outbox


def test_deletion_queue_failure_cannot_undo_login(
    client, linked, queued, sent, monkeypatch, django_capture_on_commit_callbacks
):
    ask(client)
    deliver(queued[0])

    def refuse(*args):
        raise ConnectionError("queue unavailable")

    monkeypatch.setattr(tasks, "delete_login_message", refuse, raising=False)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("login-code-verify"), {"code": code_from(sent)}, **JSON)
    assert response.json()["ok"] and client.session["_auth_user_id"] == str(linked.pk)


def test_deletion_worker_handles_a_missing_message(monkeypatch):
    from apps.accounts.telegram_codes import delete_message

    def refuse(*args):
        raise api.TelegramError(400)

    monkeypatch.setattr(api, "delete_message", refuse)
    delete_message(12345, 1)


@pytest.mark.django_db(transaction=True)
def test_sms_transport_does_not_hold_the_account_lock(rf, linked, monkeypatch):
    import threading

    from django.db import connection, transaction

    from apps.accounts import services

    results = []

    def update():
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '500ms'")
                User.objects.filter(pk=linked.pk).update(is_verified=True)
            results.append(True)
        except Exception as error:
            results.append(error)
        finally:
            connection.close()

    def send(*args):
        thread = threading.Thread(target=update)
        thread.start()
        thread.join(timeout=3)
        assert results == [True], "SMS transport held the account row lock"

    monkeypatch.setattr(sms, "send_code", send)
    services.request_login_code(rf.post("/"), PHONE)


@pytest.mark.django_db(transaction=True)
def test_real_tasks_generate_only_in_the_worker_and_delete_after_login(
    client, linked, sent, monkeypatch
):
    from huey.contrib.djhuey import HUEY

    generated = []
    new_code = otp._new_code

    def generate():
        generated.append(True)
        return new_code()

    monkeypatch.setattr(otp, "_new_code", generate)
    assert ask(client).json()["ok"] and not generated
    job = HUEY.dequeue()
    assert len(job.args) == 4 and job.args[0] == PHONE and len(job.args[1]) >= 22
    HUEY.execute(job)
    assert generated == [True] and len(sent) == 1
    response = client.post(reverse("login-code-verify"), {"code": code_from(sent)}, **JSON)
    assert response.json()["ok"]
    deletion = HUEY.dequeue()
    assert deletion.args == (linked.telegram_id, 1)
    removed = []
    monkeypatch.setattr(api, "delete_message", lambda *args: removed.append(args))
    HUEY.execute(deletion)
    assert removed == [(linked.telegram_id, 1)]

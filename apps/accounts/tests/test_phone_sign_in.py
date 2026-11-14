"""Signing in with a mobile number and a code sent by SMS (owner, 2026-11-12).

Every SMS is paid for and SMS bombers aim at Iranian sign-in forms, so each budget
here is checked at its boundary: per number, per network, and the shop's own day.
"""

from __future__ import annotations

import re

import pytest
import time_machine
from django.conf import settings
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.accounts import otp, services, views
from apps.accounts.models import User
from apps.core import ratelimit, sms

pytestmark = pytest.mark.django_db

PHONE = "09123456789"
PASSWORD = "Op3rator!passphrase"  # noqa: S105 — a fixture value, not a credential
START = 1_790_000_000


def sms_code() -> str:
    found = re.search(r"\b\d{6}\b", sms.outbox[-1][1])
    assert found, "no code in the message"
    return found.group()


def send_code(subject: str) -> otp.Issued:
    """Text a code and make it live, without the service's account lock or limits."""
    return otp._store(subject, otp.prepare_login_code(subject))


def ask(client, phone: str = PHONE, **extra):
    return client.post(reverse("login-code"), {"phone": phone}, **extra)


def at(rf, ip: str = "127.0.0.1"):
    return rf.post("/", REMOTE_ADDR=ip)


# --- Signing in ----------------------------------------------------------------


def test_a_known_and_an_unknown_number_get_the_same_answer():
    User.objects.create_user(phone="09120000001")

    known = ask(Client(), "09120000001", HTTP_ACCEPT="application/json")
    unknown = ask(Client(), "09120000002", HTTP_ACCEPT="application/json")

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_the_first_correct_code_creates_the_account_by_its_number(client):
    ask(client, "+98 912 345 6789")

    client.post(reverse("login-code-verify"), {"code": sms_code()})

    user = User.objects.get()
    assert user.phone == PHONE and user.email == "" and user.is_verified
    assert client.session["_auth_user_id"] == str(user.pk)


def test_the_code_goes_by_sms_and_no_mail_is_sent(client):
    ask(client)

    assert sms.outbox[-1][0] == PHONE
    assert not mail.outbox


def test_the_code_page_names_the_number_half_hidden(client):
    ask(client)

    body = client.get(reverse("login-code-verify")).content.decode()

    assert "۰۹۱۲•••۶۷۸۹" in body and PHONE not in body


def test_a_staff_number_gets_the_wrong_code_answer(client):
    User.objects.create_user("operator@example.test", PASSWORD, phone=PHONE, is_staff=True)
    ask(client)

    response = client.post(reverse("login-code-verify"), {"code": sms_code()})

    assert "_auth_user_id" not in client.session
    assert views.WRONG_CODE in response.content.decode()


def test_an_invalid_number_is_refused_before_anything_is_sent(client):
    response = ask(client, "12345")

    assert "شماره‌ی موبایل باید ۱۱ رقم باشد" in response.content.decode()
    assert not sms.outbox


# --- Budgets ------------------------------------------------------------------


def test_a_second_code_within_two_minutes_is_refused(rf):
    with time_machine.travel(START, tick=False) as clock:
        services.request_login_code(at(rf), PHONE)
        clock.shift(119)
        with pytest.raises(services.RateLimited) as refused:
            services.request_login_code(at(rf), PHONE)
        clock.shift(2)
        services.request_login_code(at(rf), PHONE)

    assert refused.value.message == services.CODE_COOLDOWN
    assert len(sms.outbox) == 2


def test_the_sixth_code_in_an_hour_is_refused(rf):
    with time_machine.travel(START, tick=False) as clock:
        for _ in range(5):
            services.request_login_code(at(rf), PHONE)
            clock.shift(121)
        with pytest.raises(services.RateLimited) as refused:
            services.request_login_code(at(rf), PHONE)

    assert refused.value.message == services.TOO_MANY_CODES
    assert len(sms.outbox) == 5


def test_the_eleventh_accepted_code_in_a_day_is_refused(rf):
    with time_machine.travel(START, tick=False) as clock:
        for _ in range(10):
            services.request_login_code(at(rf), PHONE)
            clock.shift(13 * 60)
        with pytest.raises(services.RateLimited) as refused:
            services.request_login_code(at(rf), PHONE)

    assert refused.value.message == services.TOO_MANY_CODES_DAILY
    assert len(sms.outbox) == 10


def test_one_network_gets_ten_codes_an_hour_and_twenty_a_day(rf):
    with time_machine.travel(START, tick=False) as clock:
        for n in range(10):
            services.request_login_code(at(rf), f"0912000{n:04d}")
        with pytest.raises(services.RateLimited) as hourly:
            services.request_login_code(at(rf), "09129999999")
        clock.shift(3601)
        for n in range(10, 20):
            services.request_login_code(at(rf), f"0912000{n:04d}")
        with pytest.raises(services.RateLimited) as daily:
            services.request_login_code(at(rf), "09129999998")

    assert hourly.value.message == services.TOO_MANY_CODES_FROM_NETWORK
    assert daily.value.message == services.TOO_MANY_CODES_FROM_NETWORK_DAILY
    assert len(sms.outbox) == 20


def test_a_failed_send_spends_no_daily_unit_and_logs_neither_number_nor_code(
    rf, monkeypatch, caplog
):
    sent = []

    def refuse(mobile, code):
        sent.append(code)
        raise sms.SmsNotSent(500, 0)

    monkeypatch.setattr(sms, "send_code", refuse)

    with pytest.raises(services.CodeNotSent):
        services.request_login_code(at(rf), PHONE)

    assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, PHONE) == 10
    assert ratelimit.remaining(ratelimit.SMS_DAILY_BUDGET, "shop") == 300
    assert sent and sent[-1] not in caplog.text and PHONE not in caplog.text
    assert "SMS" in caplog.text


def test_the_hundredth_sms_of_the_day_tells_the_operator_once(rf):
    for _ in range(ratelimit.SMS_ALERT_AT - 1):
        ratelimit.hit(ratelimit.SMS_DAILY_BUDGET, "shop")

    services.request_login_code(at(rf), PHONE)
    services.request_login_code(at(rf, "10.0.0.2"), "09120000001")

    alerts = [message for message in mail.outbox if message.to == [settings.SUPPORT_EMAIL]]
    assert len(alerts) == 1
    assert "۱۰۰" in alerts[0].body


def test_the_next_days_hundredth_sms_tells_the_operator_again(rf):
    with time_machine.travel("2026-11-03 08:00:00", tick=False) as clock:
        for _ in range(ratelimit.SMS_ALERT_AT - 1):
            ratelimit.hit(ratelimit.SMS_DAILY_BUDGET, "shop")
        clock.shift(12 * 60 * 60)
        services.request_login_code(at(rf), PHONE)
        # The day that opened at 08:00 is over by 09:00 the next morning.
        clock.shift(13 * 60 * 60)
        services.request_login_code(at(rf, "10.0.0.2"), "09120000001")
        for _ in range(ratelimit.SMS_ALERT_AT - 2):
            ratelimit.hit(ratelimit.SMS_DAILY_BUDGET, "shop")
        services.request_login_code(at(rf, "10.0.0.3"), "09120000002")

    alerts = [message for message in mail.outbox if message.to == [settings.SUPPORT_EMAIL]]
    assert len(alerts) == 2


def test_past_the_days_ceiling_no_code_is_sent(rf):
    for _ in range(ratelimit.SMS_DAILY_BUDGET.times):
        ratelimit.hit(ratelimit.SMS_DAILY_BUDGET, "shop")

    with pytest.raises(services.CodeNotSent) as refused:
        services.request_login_code(at(rf), PHONE)

    assert "متوقف" in refused.value.text
    assert not sms.outbox
    assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_PHONE_COOLDOWN, PHONE) == 1


def test_a_code_lives_ten_minutes_and_burns_after_five_wrong_guesses():
    issued = send_code(PHONE)
    wrong = f"{(int(issued.code) + 1) % 10**6:06d}"

    for _ in range(otp.MAX_ATTEMPTS):
        assert not otp.verify(PHONE, wrong)

    assert issued.ttl_minutes == 10
    assert not otp.verify(PHONE, issued.code), "a guessed-out code came back"


# --- The panel and the bot ------------------------------------------------------


def test_the_panel_signs_staff_in_by_phone_and_password(client):
    User.objects.create_user("operator@example.test", PASSWORD, phone=PHONE, is_staff=True)

    client.post(reverse("admin:login"), {"username": "0912 345 6789", "password": PASSWORD})

    assert "_auth_user_id" in client.session


def test_the_panel_does_not_take_an_email_for_a_username(client):
    User.objects.create_user("operator@example.test", PASSWORD, phone=PHONE, is_staff=True)

    client.post(reverse("admin:login"), {"username": "operator@example.test", "password": PASSWORD})

    assert "_auth_user_id" not in client.session


def test_the_bot_names_the_account_by_its_half_hidden_number():
    from apps.orders.telegram_panel import _home

    text, _ = _home(User.objects.create_user(phone=PHONE))

    assert "۰۹۱۲•••۶۷۸۹" in text and PHONE not in text

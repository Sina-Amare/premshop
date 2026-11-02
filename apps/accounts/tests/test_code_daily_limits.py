import smtplib

import pytest
import time_machine
from django.contrib.messages import get_messages
from django.core import mail
from django.urls import reverse

from apps.accounts import otp, services
from apps.accounts.models import User
from apps.core import ratelimit
from apps.core.email_log import MailQuotaExceeded

pytestmark = pytest.mark.django_db
EMAIL = "daily@example.test"
IP = "127.0.0.1"


def budgets():
    return [
        (ratelimit.OTP_REQUESTS_PER_EMAIL_DAILY, EMAIL),
        (ratelimit.OTP_REQUESTS_PER_IP_DAILY, IP),
        (ratelimit.OTP_REQUESTS_PER_EMAIL, EMAIL),
        (ratelimit.OTP_REQUESTS_PER_IP, IP),
    ]


@pytest.mark.parametrize("blocked", range(4))
@pytest.mark.parametrize("recovery", [False, True])
def test_each_refusal_has_its_own_message_and_spends_no_other_budget(client, blocked, recovery):
    if recovery:
        client.force_login(User.objects.create_user(EMAIL))
    limit, subject = budgets()[blocked]
    for _ in range(limit.times):
        ratelimit.hit(limit, subject)
    before = [ratelimit.remaining(*budget) for budget in budgets()]
    path = "account-password-recovery" if recovery else "login-code"
    response = client.post(
        reverse(path), {"email": EMAIL, "action": "request"}, HTTP_ACCEPT="application/json"
    )
    assert response.status_code == 429
    expected = [
        "درخواست کد برای این ایمیل به سقف روزانه رسیده است. ۲۴ ساعت دیگر دوباره امتحان کنید.",
        "درخواست کد از این شبکه به سقف روزانه رسیده است. ۲۴ ساعت دیگر دوباره امتحان کنید.",
        services.TOO_MANY_CODES,
        services.TOO_MANY_CODES_FROM_NETWORK,
    ][blocked]
    if recovery:
        assert expected in [str(message) for message in get_messages(response.wsgi_request)]
    else:
        assert response.json()["error"] == expected
    assert [ratelimit.remaining(*budget) for budget in budgets()] == before
    assert not mail.outbox


def test_daily_refusal_precedes_exhausted_short_windows(rf):
    for limit, subject in budgets():
        for _ in range(limit.times):
            ratelimit.hit(limit, subject)
    with pytest.raises(services.RateLimited, match="سقف روزانه"):
        services.request_login_code(rf.post("/"), EMAIL)


@pytest.mark.parametrize("failure", [smtplib.SMTPException, MailQuotaExceeded, "zero"])
@pytest.mark.parametrize("recovery", [False, True])
def test_an_unsent_code_spends_short_windows_but_no_daily_unit(
    client, rf, monkeypatch, failure, recovery
):
    def refuse(*args, **kwargs):
        if failure == "zero":
            return 0
        raise failure

    monkeypatch.setattr("apps.core.email.send_templated_email", refuse)
    if recovery:
        client.force_login(User.objects.create_user(EMAIL))
    if failure is MailQuotaExceeded and recovery:
        assert (
            client.post(reverse("account-password-recovery"), {"action": "request"}).status_code
            == 503
        )
    else:
        with pytest.raises(
            MailQuotaExceeded if failure is MailQuotaExceeded else smtplib.SMTPException
        ):
            if recovery:
                client.post(reverse("account-password-recovery"), {"action": "request"})
            else:
                services.request_login_code(rf.post("/"), EMAIL)
    assert [ratelimit.remaining(*budget) for budget in budgets()] == [10, 20, 2, 9]


def test_daily_email_is_shared_normalized_not_reset_on_login_and_expires(client, rf):
    user = User.objects.create_user(EMAIL, "fixture-passphrase-827!")
    client.force_login(user)
    with time_machine.travel("2026-11-03 10:00:00", tick=False) as clock:
        for index in range(10):
            if index % 2:
                assert (
                    client.post(
                        reverse("account-password-recovery"), {"action": "request"}
                    ).status_code
                    == 200
                )
            else:
                services.request_login_code(rf.post("/"), EMAIL.upper())
            clock.shift(901)
        assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_EMAIL_DAILY, EMAIL) == 0
        assert (
            services.login_with_password(rf_request(client, rf), EMAIL, "fixture-passphrase-827!")
            is not None
        )
        with pytest.raises(services.RateLimited, match="سقف روزانه"):
            services.request_login_code(rf.post("/"), EMAIL)
        clock.shift(86400 - 9010)
        services.request_login_code(rf.post("/"), EMAIL)
        assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_EMAIL_DAILY, EMAIL) == 9


def rf_request(client, rf):
    request = rf.post("/")
    request.session = client.session
    request.user = User.objects.get(email=EMAIL)
    return request


def test_twenty_daily_ip_sends_across_addresses_then_expiry(rf):
    with time_machine.travel("2026-11-03 10:00:00", tick=False) as clock:
        for index in range(20):
            services.request_login_code(rf.post("/"), f"other{index}@example.test")
            clock.shift(901)
        with pytest.raises(services.RateLimited, match="از این شبکه به سقف روزانه"):
            services.request_login_code(rf.post("/"), EMAIL)
        clock.shift(86400 - 18020)
        services.request_login_code(rf.post("/"), EMAIL)
        assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_IP_DAILY, IP) == 19


def test_daily_overshoot_after_acceptance_does_not_refuse_the_sent_code(rf, monkeypatch):
    original = otp.send_login_code

    def concurrent(*args, **kwargs):
        for limit, subject in budgets()[:2]:
            for _ in range(limit.times):
                ratelimit.hit(limit, subject)
        return original(*args, **kwargs)

    monkeypatch.setattr(otp, "send_login_code", concurrent)
    assert services.request_login_code(rf.post("/"), EMAIL).code
    assert len(mail.outbox) == 1

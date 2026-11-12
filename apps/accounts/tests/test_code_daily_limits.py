import smtplib

import pytest
import time_machine
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
def test_each_refusal_has_its_own_message_and_spends_no_other_budget(client, blocked):
    limit, subject = budgets()[blocked]
    for _ in range(limit.times):
        ratelimit.hit(limit, subject)
    before = [ratelimit.remaining(*budget) for budget in budgets()]
    response = client.post(reverse("login-code"), {"email": EMAIL}, HTTP_ACCEPT="application/json")
    assert response.status_code == 429
    expected = [
        "درخواست کد برای این ایمیل به سقف روزانه رسیده است. ۲۴ ساعت دیگر دوباره امتحان کنید.",
        "درخواست کد از این شبکه به سقف روزانه رسیده است. ۲۴ ساعت دیگر دوباره امتحان کنید.",
        services.TOO_MANY_CODES,
        services.TOO_MANY_CODES_FROM_NETWORK,
    ][blocked]
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
def test_an_unsent_code_spends_short_windows_but_no_daily_unit(rf, monkeypatch, failure):
    def refuse(*args, **kwargs):
        if failure == "zero":
            return 0
        raise failure

    monkeypatch.setattr("apps.core.email.send_templated_email", refuse)
    # A relay failure is answered on the page as the quota's refusal is, rather
    # than escaping to the general error page.
    with pytest.raises(MailQuotaExceeded if failure is MailQuotaExceeded else services.CodeNotSent):
        services.request_login_code(rf.post("/"), EMAIL)
    assert [ratelimit.remaining(*budget) for budget in budgets()] == [10, 20, 2, 9]


def test_daily_email_is_normalized_not_reset_on_login_and_expires(client, rf):
    User.objects.create_user(EMAIL)
    with time_machine.travel("2026-11-03 10:00:00", tick=False) as clock:
        for index in range(10):
            if index:
                clock.shift(901)
            issued = services.request_login_code(
                rf.post("/"), EMAIL.upper() if index % 2 else EMAIL
            )
        assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_EMAIL_DAILY, EMAIL) == 0
        assert services.complete_code_login(rf_request(client, rf), EMAIL, issued.code) is not None
        with pytest.raises(services.RateLimited, match="سقف روزانه"):
            services.request_login_code(rf.post("/"), EMAIL)
        clock.shift(86400 - 9 * 901)
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

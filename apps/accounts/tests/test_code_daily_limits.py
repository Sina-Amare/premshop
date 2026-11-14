import pytest
import time_machine
from django.urls import reverse

from apps.accounts import otp, services
from apps.accounts.models import User
from apps.accounts.tests.test_phone_sign_in import sms_code
from apps.core import ratelimit, sms

pytestmark = pytest.mark.django_db
PHONE = "09124445566"
IP = "127.0.0.1"


def budgets():
    return [
        (ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, PHONE),
        (ratelimit.OTP_REQUESTS_PER_IP_DAILY, IP),
        (ratelimit.OTP_REQUESTS_PER_PHONE_COOLDOWN, PHONE),
        (ratelimit.OTP_REQUESTS_PER_PHONE, PHONE),
        (ratelimit.OTP_REQUESTS_PER_IP, IP),
    ]


@pytest.mark.parametrize("blocked", range(5))
def test_each_refusal_has_its_own_message_and_spends_no_other_budget(client, blocked):
    limit, subject = budgets()[blocked]
    for _ in range(limit.times):
        ratelimit.hit(limit, subject)
    before = [ratelimit.remaining(*budget) for budget in budgets()]
    response = client.post(reverse("login-code"), {"phone": PHONE}, HTTP_ACCEPT="application/json")
    assert response.status_code == 429
    expected = [
        services.TOO_MANY_CODES_DAILY,
        services.TOO_MANY_CODES_FROM_NETWORK_DAILY,
        services.CODE_COOLDOWN,
        services.TOO_MANY_CODES,
        services.TOO_MANY_CODES_FROM_NETWORK,
    ][blocked]
    assert response.json()["error"] == expected
    assert [ratelimit.remaining(*budget) for budget in budgets()] == before
    assert not sms.outbox


def test_daily_refusal_precedes_exhausted_short_windows(rf):
    for limit, subject in budgets():
        for _ in range(limit.times):
            ratelimit.hit(limit, subject)
    with pytest.raises(services.RateLimited, match="سقف روزانه"):
        services.request_login_code(rf.post("/"), PHONE)


def test_an_unsent_code_spends_short_windows_but_no_daily_unit(rf, monkeypatch):
    def refuse(mobile, code):
        raise sms.SmsNotSent(500)

    monkeypatch.setattr(sms, "send_code", refuse)
    # A refused send is answered on the page, rather than escaping to the general
    # error page.
    with pytest.raises(services.CodeNotSent):
        services.request_login_code(rf.post("/"), PHONE)
    assert [ratelimit.remaining(*budget) for budget in budgets()] == [10, 20, 0, 4, 9]


def test_the_daily_budget_is_per_number_not_reset_on_login_and_expires(client, rf):
    User.objects.create_user(phone=PHONE)
    with time_machine.travel("2026-11-03 10:00:00", tick=False) as clock:
        for index in range(10):
            if index:
                clock.shift(13 * 60)
            spelling = "+98 912 444 5566" if index % 2 else PHONE
            services.request_login_code(rf.post("/"), spelling)
        assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, PHONE) == 0
        assert services.complete_code_login(rf_request(client, rf), PHONE, sms_code()) is not None
        with pytest.raises(services.RateLimited, match="سقف روزانه"):
            services.request_login_code(rf.post("/"), PHONE)
        clock.shift(86400 - 9 * 13 * 60)
        services.request_login_code(rf.post("/"), PHONE)
        assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, PHONE) == 9


def rf_request(client, rf):
    request = rf.post("/")
    request.session = client.session
    request.user = User.objects.get(phone=PHONE)
    return request


def test_twenty_daily_ip_sends_across_numbers_then_expiry(rf):
    with time_machine.travel("2026-11-03 10:00:00", tick=False) as clock:
        for index in range(20):
            services.request_login_code(rf.post("/"), f"0912000{index:04d}")
            clock.shift(901)
        with pytest.raises(services.RateLimited, match="از این شبکه به سقف روزانه"):
            services.request_login_code(rf.post("/"), PHONE)
        clock.shift(86400 - 18020)
        services.request_login_code(rf.post("/"), PHONE)
        assert ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_IP_DAILY, IP) == 19


def test_daily_overshoot_after_acceptance_does_not_refuse_the_sent_code(rf, monkeypatch):
    original = otp.prepare_login_code

    def concurrent(*args, **kwargs):
        for limit, subject in budgets()[:2]:
            for _ in range(limit.times):
                ratelimit.hit(limit, subject)
        return original(*args, **kwargs)

    monkeypatch.setattr(otp, "prepare_login_code", concurrent)
    assert services.request_login_code(rf.post("/"), PHONE).channel == "sms"
    assert len(sms.outbox) == 1
    assert otp.verify(PHONE, sms_code())

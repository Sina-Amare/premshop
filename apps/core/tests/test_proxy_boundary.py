"""The address every per-IP limit counts, and the one header behind nginx it trusts.

Behind nginx, REMOTE_ADDR is nginx itself, so the visitor's address has to come
from a header. The left-most X-Forwarded-For entry is whatever the visitor typed,
so reading it let anyone reset the sign-in, password and search limits with every
request. nginx overwrites X-Real-IP with the address it saw, so that is the one
header read, and only when the deployment says nginx is in front.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts import services
from apps.core import ratelimit

NGINX = "172.18.0.5"
VISITOR = "203.0.113.7"


def address(settings, *, trust: bool, **meta: str) -> str:
    settings.TRUST_PROXY_HEADERS = trust
    return ratelimit.client_ip(RequestFactory().get("/", REMOTE_ADDR=NGINX, **meta))


@pytest.mark.parametrize("trust", [False, True])
def test_a_forged_forwarded_for_changes_nothing(settings, trust):
    assert address(settings, trust=trust, HTTP_X_FORWARDED_FOR="198.51.100.9") == NGINX
    behind_nginx = address(
        settings, trust=trust, HTTP_X_FORWARDED_FOR="198.51.100.9", HTTP_X_REAL_IP=VISITOR
    )
    assert behind_nginx == (VISITOR if trust else NGINX)


def test_x_real_ip_is_read_only_when_the_proxy_is_trusted(settings):
    assert address(settings, trust=False, HTTP_X_REAL_IP=VISITOR) == NGINX
    assert address(settings, trust=True, HTTP_X_REAL_IP=VISITOR) == VISITOR
    assert address(settings, trust=True, HTTP_X_REAL_IP="2001:DB8::7") == "2001:db8::7"


@pytest.mark.parametrize(
    "header", ["", "unknown", "203.0.113.300", f"{VISITOR}, 198.51.100.9", f" {VISITOR}"]
)
def test_a_malformed_x_real_ip_falls_back_to_the_connection(settings, header):
    assert address(settings, trust=True, HTTP_X_REAL_IP=header) == NGINX


@pytest.mark.django_db
@pytest.mark.parametrize("trust", [False, True])
def test_forged_headers_do_not_lift_the_per_ip_code_limit(client, settings, trust):
    settings.TRUST_PROXY_HEADERS = trust
    limit = ratelimit.OTP_REQUESTS_PER_IP

    def ask(n: int):
        # Behind nginx, X-Real-IP is the one header the visitor cannot write.
        forged = {
            "HTTP_X_FORWARDED_FOR": f"198.51.100.{n}",
            "HTTP_X_REAL_IP": VISITOR if trust else f"192.0.2.{n}",
        }
        return client.post(
            reverse("login-code"), {"email": f"c{n}@example.test"}, REMOTE_ADDR=NGINX, **forged
        )

    for n in range(limit.times):
        ask(n)
    sent = len(mail.outbox)

    response = ask(limit.times)

    assert services.TOO_MANY_CODES_FROM_NETWORK in response.content.decode()
    assert len(mail.outbox) == sent

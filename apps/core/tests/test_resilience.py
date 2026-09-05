"""What the app does when its infrastructure is not there.

Login codes and every rate-limit counter live in Redis, so a Redis outage really
does break authentication — there is no failing open, because failing open means
unlimited guesses at a six-digit code. What must never happen is what happened
before these guards existed: a customer clicked "send me a code" and got a Django
traceback listing the settings, the database URL and the whole environment.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError

pytestmark = pytest.mark.django_db


def test_healthz_reports_the_cache(client):
    response = client.get(reverse("healthz"))

    assert response.status_code == 200
    assert response.json()["checks"]["cache"] == "ok"


def test_healthz_goes_degraded_when_the_cache_is_unreachable(client):
    """Regression: healthz reported 'ok' while Redis was down and nobody could log
    in. A monitor that stays green through an auth outage is worse than none."""
    with mock.patch("django.core.cache.cache.set", side_effect=RedisConnectionError("refused")):
        response = client.get(reverse("healthz"))

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["cache"].startswith("error:")
    assert body["checks"]["database"] == "ok", "the database is fine and must still say so"


def test_a_cache_outage_renders_a_persian_page_not_a_traceback(client):
    """Reintroduce the bug and watch the guard fire — a guard that has never gone
    red is a guard nobody has tested."""
    with mock.patch(
        "apps.accounts.ratelimit.cache.add", side_effect=RedisConnectionError("refused")
    ):
        response = client.post(reverse("login-code"), {"email": "someone@example.test"})

    assert response.status_code == 503
    body = response.content.decode()
    assert "اختلال موقت" in body
    assert "Traceback" not in body
    assert "DATABASE_URL" not in body, "a debug page would print the whole environment"


def test_the_outage_page_offers_a_way_back(client):
    with mock.patch(
        "apps.accounts.ratelimit.cache.add", side_effect=RedisConnectionError("refused")
    ):
        body = client.post(reverse("login-code"), {"email": "x@example.test"}).content.decode()

    assert reverse("login-code") in body, "a dead end is not an error page"

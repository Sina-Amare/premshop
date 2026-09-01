"""Health endpoint and the style-guide page."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
class TestHealthz:
    def test_reports_ok_when_the_database_answers(self, client: Client) -> None:
        response = client.get(reverse("healthz"))
        assert response.status_code == 200
        payload = json.loads(response.content)
        assert payload["status"] == "ok"
        assert payload["checks"]["database"] == "ok"

    def test_reports_503_when_the_database_is_unreachable(self, client: Client) -> None:
        # A monitor must be told "do not send traffic here", not given a cheerful 200.
        with mock.patch("apps.core.views.connection.cursor", side_effect=OSError("down")):
            response = client.get(reverse("healthz"))
        assert response.status_code == 503
        assert json.loads(response.content)["status"] == "degraded"

    def test_is_never_cached(self, client: Client) -> None:
        # A cached health check reports the past, which is worse than no check.
        response = client.get(reverse("healthz"))
        assert "no-cache" in response.headers.get("Cache-Control", "")


class TestStyleguide:
    def test_renders_the_design_system(self, client: Client) -> None:
        response = client.get(reverse("styleguide"))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'dir="rtl"' in body
        assert 'lang="fa"' in body
        # Prices render in Persian digits, grouped with an ASCII comma.
        assert "۱,۲۰۰,۰۰۰" in body

    def test_uses_no_external_resources(self, client: Client) -> None:
        # Iranian visitors cannot reach foreign CDNs: every asset must be local.
        body = client.get(reverse("styleguide")).content.decode()
        for forbidden in ("https://fonts.googleapis.com", "https://cdn.", "//unpkg.com"):
            assert forbidden not in body

    def test_price_numeral_and_currency_are_separate_elements(self, client: Client) -> None:
        # They differ in family, size and weight by design, so one string
        # cannot express them — a currency word at the numeral's weight is
        # the reliable amateur tell (ADR-0016).
        body = client.get(reverse("styleguide")).content.decode()
        assert '<span class="price__num">' in body
        assert '<span class="price__cur">تومان</span>' in body

    def test_free_price_reads_as_free(self, client: Client) -> None:
        # The "never ۰ تومان" rule is asserted against the filter itself in
        # test_formatting: as a substring of a rendered page it also matches
        # the tail of ۱,۲۰۰,۰۰۰ تومان, which is a false positive.
        body = client.get(reverse("styleguide")).content.decode()
        assert '<span class="price__num">رایگان</span>' in body

    def test_ledger_signature_is_present(self, client: Client) -> None:
        body = client.get(reverse("styleguide")).content.decode()
        assert 'class="fact__lead"' in body

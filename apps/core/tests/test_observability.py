"""The error-report scrubber.

A privacy control nobody tests is a privacy control nobody has. These tests
are the proof that a crash cannot carry a customer's credential to a third
party (ADR-0007, ADR-0015).
"""

from __future__ import annotations

import pytest

from apps.core.observability import REDACTED, redact_url, scrub, scrub_event


class TestScrubKeys:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "PASSWORD",
            "new_password2",
            "secret_key",
            "api_token",
            "otp_code",
            "authorization",
            "csrf_token",
            "customer_input",
            "value",
            "card_number",
            "sheba",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        assert scrub({key: "hunter2"})[key] == REDACTED

    def test_ordinary_keys_survive(self) -> None:
        event = {"order_number": 1041, "product": "Claude Pro", "amount": 1_200_000}
        assert scrub(event) == event

    def test_nested_structures_are_walked(self) -> None:
        event = {
            "exception": {
                "values": [{"stacktrace": {"frames": [{"vars": {"password": "hunter2", "n": 3}}]}}]
            }
        }
        frame = scrub(event)["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert frame["vars"]["password"] == REDACTED
        assert frame["vars"]["n"] == 3

    def test_sentry_structural_keys_survive(self) -> None:
        # Regression: a substring rule on "value" also matched Sentry's own
        # `exception.values`, redacting the report structure and leaving us
        # blind to the error. Structure must survive; only leaves are redacted.
        event = {"exception": {"values": [{"type": "ValueError", "value": "boom"}]}}
        scrubbed = scrub(event)
        assert isinstance(scrubbed["exception"]["values"], list)
        assert scrubbed["exception"]["values"][0]["type"] == "ValueError"
        # ...while `value` itself, which is where a credential would sit, is gone.
        assert scrubbed["exception"]["values"][0]["value"] == REDACTED

    def test_diagnostic_keys_that_merely_look_similar_survive(self) -> None:
        # "author" is not "auth"; a status code is diagnostics, not a secret.
        event = {"author": "sina", "status_code": 500, "country_code": "IR"}
        assert scrub(event) == event

    @pytest.mark.parametrize("key", ["decrypted_value", "decryptedValue", "field_value", "code"])
    def test_credential_bearing_variants_are_caught(self, key: str) -> None:
        # Word-boundary matching, so a credential in a local variable is caught
        # whichever naming style it arrived in.
        assert scrub({key: "sk-live-abc"})[key] == REDACTED

    def test_deeply_nested_input_cannot_exhaust_the_stack(self) -> None:
        payload: dict = {"k": "v"}
        for _ in range(50):
            payload = {"nested": payload}
        assert scrub(payload) is not None  # truncates rather than recursing forever


class TestScrubDeliveryLinks:
    def test_token_is_stripped_from_a_delivery_link(self) -> None:
        url = "https://premshop.ir/d/8f3aQ2_Zxc9LkPmN7bVr/"
        assert "8f3aQ2_Zxc9LkPmN7bVr" not in redact_url(url)
        assert redact_url(url) == f"https://premshop.ir/d/{REDACTED}/"

    def test_other_urls_are_untouched(self) -> None:
        url = "https://premshop.ir/orders/1041/"
        assert redact_url(url) == url

    def test_token_inside_a_message_string_is_stripped(self) -> None:
        message = "failed rendering /d/SECRETTOKEN123/ for item 5"
        assert "SECRETTOKEN123" not in scrub(message)


class TestScrubEvent:
    def test_request_body_and_cookies_are_dropped(self) -> None:
        event = {
            "request": {
                "url": "https://premshop.ir/d/LIVETOKEN/",
                "data": {"password": "hunter2", "note": "harmless"},
                "cookies": {"sessionid": "abc123"},
                "method": "POST",
            }
        }
        request = scrub_event(event)["request"]
        assert "data" not in request
        assert "cookies" not in request
        assert "LIVETOKEN" not in request["url"]
        assert request["method"] == "POST"

    def test_event_is_kept_not_discarded(self) -> None:
        # We still want to know the error happened.
        assert scrub_event({"message": "boom"})["message"] == "boom"

    def test_realistic_delivery_failure_leaks_nothing(self) -> None:
        event = {
            "message": "delivery failed",
            "request": {"url": "https://premshop.ir/d/TOKEN_X/", "data": {"value": "netflix-pw"}},
            "extra": {"customer_input": "user@example.com / mypassword", "item_id": 12},
            "exception": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [
                                {"vars": {"decrypted_value": "sk-live-abc", "password": "pw"}}
                            ]
                        }
                    }
                ]
            },
        }
        rendered = repr(scrub_event(event))
        for secret in ("netflix-pw", "mypassword", "sk-live-abc", "TOKEN_X"):
            assert secret not in rendered
        assert "item_id" in rendered

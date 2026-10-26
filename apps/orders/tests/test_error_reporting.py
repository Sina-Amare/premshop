import json
import secrets

import sentry_sdk
from sentry_sdk.transport import Transport

from apps.core.observability import sentry_options


def test_crash_with_unlabelled_plaintext_does_not_send_it_to_error_reporting():
    captured = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    captured.append(item.payload.json)

    secret_value = secrets.token_urlsafe(24)

    def fail(value):
        plaintext = value
        assert plaintext
        raise RuntimeError("Decryption failed")

    client = sentry_sdk.Client(
        **sentry_options(dsn="https://public@example.test/1", environment="test"),
        transport=Capture(),
        default_integrations=False,
    )
    with sentry_sdk.isolation_scope() as scope:
        scope.set_client(client)
        try:
            fail(secret_value)
        except RuntimeError:
            sentry_sdk.capture_exception()
    client.close()
    assert len(captured) == 1
    assert secret_value not in json.dumps(captured)

import io
import logging

import pytest

from apps.core.observability import redact_url, scrub_event


@pytest.mark.parametrize(
    "path",
    [
        "/track/TOKEN-EXAMPLE_123/",
        "/%74rack/TOKEN-EXAMPLE_123/",
        "/login/?next=%252F%2574rack%252FTOKEN-EXAMPLE_123%252F",
        "/TRACK/TOKEN-EXAMPLE_123/",
        "/login/?next=%2Ftrack%2FTOKEN-EXAMPLE_123%2F",
    ],
)
def test_tracking_tokens_are_removed_from_error_reports_and_nested_links(path):
    assert "TOKEN-EXAMPLE_123" not in redact_url(path)
    event = {"request": {"url": "https://example.test" + path}, "message": "Failed " + path}
    assert "TOKEN-EXAMPLE_123" not in repr(scrub_event(event))


def test_configured_access_and_exception_logs_redact_tracking_links():
    path = "/track/LOG-TOKEN_123/"
    for name in ("django.server", "django.request", "apps.core.middleware"):
        logger = logging.getLogger(name)
        handlers = logger.handlers or logging.getLogger().handlers
        handler = next(h for h in handlers if isinstance(h, logging.StreamHandler))
        stream = io.StringIO()
        previous = handler.stream
        handler.setStream(stream)
        try:
            try:
                raise RuntimeError("Failed on " + path)
            except RuntimeError:
                logger.error("Request failed: %s", path, exc_info=True)
            assert "LOG-TOKEN_123" not in stream.getvalue()
            assert "RuntimeError" in stream.getvalue()
        finally:
            handler.setStream(previous)

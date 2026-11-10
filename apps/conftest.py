"""Fixtures for every test in the project."""

import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from playwright.sync_api import Browser, Error, sync_playwright

REPOSITORY = Path(__file__).resolve().parent.parent
# A test's output belongs in tmp_path; these are where it used to land in the working tree.
OUTPUT_FOLDERS = ("screenshots", ".playwright-mcp")


def written_output() -> dict[str, int]:
    return {
        path.relative_to(REPOSITORY).as_posix(): path.stat().st_mtime_ns
        for folder in OUTPUT_FOLDERS
        for path in (REPOSITORY / folder).rglob("*")
        if path.is_file()
    }


@pytest.fixture(autouse=True, scope="session")
def no_output_in_the_repository() -> Iterator[None]:
    before = written_output()
    yield
    written = sorted(path for path, mtime in written_output().items() if before.get(path) != mtime)
    if written:
        pytest.fail(f"tests wrote into the repository instead of tmp_path: {written[:10]}")


class Answering(threading.Condition):
    requests = 0


def answering(server: Any) -> Answering:
    """Counts the requests the live server is in the middle of.

    Requests, not connections: an idle keep-alive connection, such as the one a
    page's APIRequestContext leaves open, holds nothing in the database.
    """
    if not hasattr(server, "answering"):
        server.answering = Answering()
        app = server.get_app()

        # shortcut: counts from the WSGI app, so a request whose bytes arrived but whose thread has not got here reads 0, upgrade to counting in the socket server if a flush still races
        def counted(environ: Any, start_response: Any) -> Any:
            with server.answering:
                server.answering.requests += 1
            try:
                return app(environ, start_response)
            finally:
                with server.answering:
                    server.answering.requests -= 1
                    server.answering.notify_all()

        server.set_app(counted)
    return server.answering


@pytest.fixture(autouse=True)
def live_server_finishes_before_the_flush(request: Any) -> Iterator[None]:
    """A request the live server is still answering when a test ends, such as a
    page's availability poll, would race the flush that empties the database next:
    TRUNCATE deadlocks against its queries, or it writes into the next test.

    pytest-django's autouse fixtures set up the database first, so this one is torn
    down before the flush.
    """
    if "live_server" not in request.fixturenames:
        yield
        return
    requests = answering(request.getfixturevalue("live_server").thread.httpd)
    yield
    with requests:
        if not requests.wait_for(lambda: requests.requests == 0, timeout=30):
            pytest.fail(f"the live server is still answering {requests.requests} request(s)")


@pytest.fixture(autouse=True)
def memory_tasks(monkeypatch: Any) -> None:
    from huey.contrib.djhuey import HUEY
    from huey.storage import MemoryStorage

    monkeypatch.setattr(HUEY, "storage", MemoryStorage(HUEY.name))


@pytest.fixture(autouse=True)
def telegram_off(settings: Any) -> None:
    """Telegram is opt-in per test, whatever bot token the local .env holds."""
    settings.TELEGRAM_BOT_TOKEN = ""  # nosec B105 - an empty token switches Telegram off


@pytest.fixture(autouse=True)
def ordinary_catalog(settings: Any) -> None:
    """Review notices are opt-in, independent of the local preview's .env."""
    settings.DEMO_CATALOG = False


@pytest.fixture(autouse=True)
def clean_mail_accounting(
    settings: Any, request: Any, django_db_blocker: Any, monkeypatch: Any
) -> Iterator[None]:
    from apps.core import email_log

    settings.EMAIL_QUOTA_RESET_DATE = ""
    settings.EMAIL_QUOTA_STARTING_USAGE = 0
    used = False
    original = email_log.reserve

    def reserve(*args: Any, **kwargs: Any) -> list[int]:
        nonlocal used
        used = True
        return original(*args, **kwargs)

    monkeypatch.setattr(email_log, "reserve", reserve)
    yield
    if used and (
        request.node.get_closest_marker("django_db")
        or {"db", "transactional_db"}.intersection(request.fixturenames)
    ):
        from django.db import connection

        # Mail writes commit outside pytest's rollback transaction too.
        with django_db_blocker.unblock():
            independent = connection.copy()
            try:
                with independent.cursor() as cursor:
                    cursor.execute("DELETE FROM core_mailattempt")
            finally:
                independent.close()


@pytest.fixture(autouse=True)
def clean_cache(settings: Any) -> Iterator[None]:
    """Every test runs against an empty in-memory cache, never a real Redis.

    Login codes, rate-limit counters and the /healthz cache probe all use the
    cache. Two things follow. A limit spent in one test would lock out the next,
    and the failure would look like a bug in the code under test. And CI has a
    Postgres service but no Redis, so any test that reaches the real backend passes
    on a developer machine and fails in the gate — which is exactly what happened
    for the first seventeen red runs of this project.
    """
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def fast_password_hashing(settings: Any) -> None:
    """PBKDF2 at a million iterations costs ~700ms per check, by design.

    That is correct in production and pointless in tests: the suite pays it on
    every sign-in, the panel's included. Nothing asserts on hashing cost, so the
    fast hasher changes no outcome, only the wait.
    """
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    """Playwright's Chromium, launched once per test module.

    Without Chromium a browser test skips, unless REQUIRE_BROWSER is set. CI and
    scripts/check.py set it, because there a skipped browser test is a gate that
    never ran and still reports green.
    """
    with pytest.MonkeyPatch.context() as patch, sync_playwright() as playwright:
        # The sync API runs an event loop, and Django then refuses ORM calls
        # "from an async context". Django documents this flag for exactly this.
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        try:
            chromium = playwright.chromium.launch()
        except Error as exc:
            if os.environ.get("REQUIRE_BROWSER"):
                pytest.fail("REQUIRE_BROWSER is set, so a browser test may not skip", pytrace=False)
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        yield chromium
        chromium.close()


@pytest.fixture
def queries_for(client: Any) -> Callable[..., tuple[int, Any]]:
    """GET a page and say how many queries it ran, with its response.

    The page is fetched once first so one-off caches are warm: what is counted
    is the page's own cost. A test compares the count at N rows with the count
    at 3N, and a page whose cost grows with its rows is the N+1 this exists to
    catch.
    """

    def count(url: str, data: dict[str, Any] | None = None) -> tuple[int, Any]:
        client.get(url, data)
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url, data)
        if response.status_code != 200:
            pytest.fail(f"{url} answered {response.status_code}, so its queries say nothing")
        return len(queries), response

    return count

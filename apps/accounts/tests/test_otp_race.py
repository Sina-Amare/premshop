"""Login codes checked by requests that arrive at the same instant.

`otp.verify` reads a code's record and then acts on it. Under real traffic two
requests can both read before either acts, and that window is rare enough that
no load test would find it reliably. Here it happens every time: the cache, as
`otp` sees it, holds each request just after its read until the other side has
reached the same point.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from django.core.cache import cache

from apps.accounts import otp

EMAIL = "race@example.test"


class HeldAfterRead:
    """Hold each thread's first read while the competing operation runs."""

    def __init__(self, *barriers: threading.Barrier) -> None:
        self.barriers = barriers
        self.readers: set[int] = set()
        self.lock = threading.Lock()

    def get(self, *args: object, **kwargs: object) -> object:
        value = cache.get(*args, **kwargs)
        with self.lock:
            first_read = threading.get_ident() not in self.readers
            self.readers.add(threading.get_ident())
        if first_read:
            for barrier in self.barriers:
                barrier.wait(timeout=5)
        return value

    def __getattr__(self, name: str) -> object:
        return getattr(cache, name)


def at_once(*calls: Callable[[], object]) -> list[object]:
    """Run each call in its own thread; an exception comes back as the result."""
    results: list[object] = [None] * len(calls)

    def run(index: int, call: Callable[[], object]) -> None:
        try:
            results[index] = call()
        except Exception as exc:  # noqa: BLE001 — reported through the result
            results[index] = exc

    threads = [threading.Thread(target=run, args=pair) for pair in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return results


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**otp.CODE_LENGTH:0{otp.CODE_LENGTH}d}"


def test_one_code_signs_in_once_when_two_requests_carry_it(monkeypatch):
    """A single-use code that two simultaneous requests can both spend is two
    sessions from one mail."""
    issued = otp.issue(EMAIL)
    monkeypatch.setattr(otp, "cache", HeldAfterRead(threading.Barrier(2)))

    results = at_once(
        lambda: otp.verify(EMAIL, issued.code), lambda: otp.verify(EMAIL, issued.code)
    )

    assert sorted(results, key=str) == [False, True], results


def test_simultaneous_wrong_guesses_are_each_counted(monkeypatch):
    """Two guesses read the same count and wrote back the same count plus one,
    so a burst of parallel guesses bought more tries than MAX_ATTEMPTS."""
    issued = otp.issue(EMAIL)
    monkeypatch.setattr(otp, "cache", HeldAfterRead(threading.Barrier(2)))
    guess = wrong(issued.code)
    at_once(lambda: otp.verify(EMAIL, guess), lambda: otp.verify(EMAIL, guess))
    monkeypatch.setattr(otp, "cache", cache)

    for _ in range(otp.MAX_ATTEMPTS - 2):
        otp.verify(EMAIL, guess)

    assert not otp.verify(EMAIL, issued.code), "the code outlived its budget of guesses"


def test_a_guess_at_the_old_code_cannot_undo_a_resend(monkeypatch):
    """The guess read the old record, the customer asked for a new code, and the
    guess then saved the old record back over it: the code just mailed stopped
    working."""
    old = otp.issue(EMAIL)
    has_read, resent = threading.Barrier(2), threading.Barrier(2)
    monkeypatch.setattr(otp, "cache", HeldAfterRead(has_read, resent))
    guess = threading.Thread(target=otp.verify, args=(EMAIL, wrong(old.code)))
    guess.start()

    has_read.wait(timeout=5)
    new = otp.issue(EMAIL)
    resent.wait(timeout=5)
    guess.join(timeout=10)
    monkeypatch.setattr(otp, "cache", cache)

    assert otp.verify(EMAIL, new.code), "the code just mailed must work"


@pytest.mark.parametrize("readers", [1, 2])
def test_a_resend_invalidates_correct_checks_already_in_flight(monkeypatch, readers):
    old = otp.issue(EMAIL)
    has_read, resent = threading.Barrier(readers + 1), threading.Barrier(readers + 1)
    monkeypatch.setattr(otp, "cache", HeldAfterRead(has_read, resent))
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(otp.verify(EMAIL, old.code)))
        for _ in range(readers)
    ]
    for thread in threads:
        thread.start()
    has_read.wait(timeout=5)
    new = otp.issue(EMAIL)
    resent.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    monkeypatch.setattr(otp, "cache", cache)

    assert results == [False] * readers, "A replaced issuance must not create a session"
    valid = otp.verify(EMAIL, new.code)
    assert valid, "An old check must not consume the replacement"


def test_a_final_wrong_guess_cannot_delete_a_replacement(monkeypatch):
    old = otp.issue(EMAIL)
    for _ in range(otp.MAX_ATTEMPTS - 1):
        assert not otp.verify(EMAIL, wrong(old.code))
    counted, resent = threading.Event(), threading.Event()
    count_attempt = otp._count_attempt

    def held_count(*args):
        result = count_attempt(*args)
        counted.set()
        assert resent.wait(timeout=5)
        return result

    monkeypatch.setattr(otp, "_count_attempt", held_count)
    results = []
    thread = threading.Thread(target=lambda: results.append(otp.verify(EMAIL, wrong(old.code))))
    thread.start()
    assert counted.wait(timeout=5)
    new = otp.issue(EMAIL)
    resent.set()
    thread.join(timeout=10)
    assert not thread.is_alive() and results == [False]
    monkeypatch.setattr(otp, "_count_attempt", count_attempt)

    valid = otp.verify(EMAIL, new.code)
    assert valid, "An exhausted old issuance must not delete the new one"


def test_a_record_from_before_issuance_keys_fails_closed():
    cache.set(otp._key(EMAIL), {"digest": otp._digest("123456"), "expires_at": 9999999999})
    valid = otp.verify(EMAIL, "123456")
    assert not valid

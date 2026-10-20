"""Fixtures for the core tests."""

from __future__ import annotations

import importlib
import os
import sys

import pytest

import config.settings.base as base


@pytest.fixture
def reload_base():
    """Re-import base.py under a patched environment, then put the world back.

    monkeypatch is not usable here: its teardown runs after this fixture's, so the
    restoring reload would still see the patched environment.
    """
    original = dict(os.environ)

    def _reload(**overrides: str):
        os.environ.update(overrides)
        return importlib.reload(base)

    yield _reload

    os.environ.clear()
    os.environ.update(original)
    sys.modules.pop("config.settings.prod", None)
    importlib.reload(base)

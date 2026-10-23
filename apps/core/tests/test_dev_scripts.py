"""`scripts/check.py --docs` stays quick only for text, and `scripts/ci.py` waits for every run."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from django.conf import settings


def _module(name: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(settings.BASE_DIR) / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _module("check")
ci = _module("ci")


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/development.md", "README.md"], True),
        ([], True),
        (["docs/state-machine.md", "apps/catalog/models.py"], False),
        (["templates/home.html"], False),
        (None, False),
    ],
)
def test_the_quick_check_is_only_for_text(paths, expected):
    assert check.text_only(paths) is expected


def test_the_quick_check_quotes_pytests_last_line():
    output = b"....\n\n1 passed in 0.31s\n\n"

    assert check.summary(output) == "1 passed in 0.31s"


def _run(run_id: int, status: str = "completed", conclusion: str = "success") -> dict:
    return {"databaseId": run_id, "event": "push", "status": status, "conclusion": conclusion}


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ([], [], False),
        ([], [_run(1)], False),
        ([_run(1)], [_run(1)], True),
        ([_run(1)], [_run(1), _run(2)], False),
        ([_run(1), _run(2, "in_progress", "")], [_run(1), _run(2, "in_progress", "")], False),
    ],
    ids=["nothing yet", "seen once", "seen twice", "a second run appeared", "one still running"],
)
def test_ci_waits_until_the_same_runs_are_complete_twice(previous, current, expected):
    assert ci.settled(previous, current) is expected

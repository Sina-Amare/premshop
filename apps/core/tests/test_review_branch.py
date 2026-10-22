"""`scripts/review_branch.py`: each rule fires on the change it exists for, and stays quiet otherwise.

Every case is a branch off one small repository, so a rule that cannot fail shows up here
as a case that passes for the wrong reason.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from django.conf import settings

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "review_branch.py"


def _module():
    spec = importlib.util.spec_from_file_location("review_branch", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rb = _module()

Files = dict[str, str]

BASE: Files = {
    "apps/shop/migrations/__init__.py": "",
    "apps/shop/migrations/0001_initial.py": (
        "from django.db import migrations\n\n\nclass Migration(migrations.Migration):\n"
        "    operations = []\n"
    ),
    "apps/shop/tests/test_views.py": (
        "PRODUCT_PAGE_QUERY_BUDGET = 8\n\n\ndef test_one():\n    assert True\n\n\n"
        "def test_two():\n    assert True\n"
    ),
    "apps/shop/views.py": "def home():\n    return 1\n",
    "apps/accounts/services.py": "def sign_in():\n    return None\n",
    "docs/decisions/0001-stack.md": "# ADR-0001\n\nDecision.\n",
    "pyproject.toml": (
        '[project]\nname = "x"\ndependencies = ["django"]\n\n'
        '[tool.pytest.ini_options]\naddopts = "--strict-markers"\n'
    ),
    "templates/home.html": "<p>home</p>\n",
    ".github/workflows/ci.yml": "name: CI\n",
}
FORWARDS = (
    "from django.db import migrations\n\n\ndef forwards(apps, schema_editor):\n    pass\n\n\n"
    "class Migration(migrations.Migration):\n    operations = [{op}]\n"
)
TEST_TWO = "\n\ndef test_two():\n    assert True\n"


def edit(path: str, old: str, new: str) -> Callable[[Files], Files]:
    return lambda files: files | {path: files[path].replace(old, new, 1)}


def add(path: str, text: str) -> Callable[[Files], Files]:
    return lambda files: files | {path: text}


def move_test_two(files: Files) -> Files:
    moved = edit("apps/shop/tests/test_views.py", TEST_TWO, "")(files)
    return moved | {"apps/shop/tests/test_other.py": "def test_two():\n    assert True\n"}


def irreversible_migration(op: str) -> Callable[[Files], Files]:
    return add("apps/shop/migrations/0002_data.py", FORWARDS.format(op=op))


CASES = {
    # name: (change, expected status, a phrase the report must contain)
    "clean": (edit("apps/shop/views.py", "1", "2"), 0, ""),
    "migration-edited": (
        edit("apps/shop/migrations/0001_initial.py", "[]", "[None]"),
        2,
        "applied migration",
    ),
    "test-removed": (edit("apps/shop/tests/test_views.py", TEST_TWO, ""), 2, "test_two"),
    "test-moved": (move_test_two, 0, ""),
    "test-renamed": (
        edit("apps/shop/tests/test_views.py", "def test_two", "def test_second"),
        1,
        "renamed",
    ),
    "skip-added": (
        edit("apps/shop/tests/test_views.py", "def test_one", "@pytest.mark.skip\ndef test_one"),
        2,
        "skipped",
    ),
    "markers-quoted": (
        edit(
            "apps/shop/tests/test_views.py",
            "PRODUCT_PAGE",
            'NOTE = "@pytest.mark.skip and # noqa are what the rules look for"\nPRODUCT_PAGE',
        ),
        0,
        "",
    ),
    "budget-raised": (
        edit("apps/shop/tests/test_views.py", "= 8", "= 15"),
        2,
        "raised from 8 to 15",
    ),
    "ci-changed": (edit(".github/workflows/ci.yml", "CI", "CI lite"), 2, "gate itself"),
    "pytest-config": (
        edit("pyproject.toml", "--strict-markers", "-k 'not slow'"),
        2,
        "tool.pytest.ini_options",
    ),
    "dependency": (
        edit("pyproject.toml", '["django"]', '["django", "requests"]'),
        1,
        "Dependencies changed",
    ),
    "adr-rewritten": (
        edit("docs/decisions/0001-stack.md", "Decision.", "Another decision."),
        2,
        "decision record",
    ),
    "adr-amended": (
        edit("docs/decisions/0001-stack.md", "Decision.\n", "Decision.\n\n**Amendment.** Later.\n"),
        0,
        "",
    ),
    "secret": (
        edit("apps/shop/views.py", "return 1", 'return "sk-' + "x" * 24 + '"'),
        2,
        "looks like a secret",
    ),
    "env-file": (add("config/.env", "SECRET_KEY=x\n"), 2, "holds secrets"),
    "irreversible": (
        irreversible_migration("migrations.RunPython(forwards)"),
        2,
        "cannot be reversed",
    ),
    "reversible": (
        irreversible_migration("migrations.RunPython(forwards, migrations.RunPython.noop)"),
        1,
        "New migration",
    ),
    "silenced": (edit("apps/shop/views.py", "return 1", "return 1  # noqa: E501"), 1, "silenced"),
    "template": (edit("templates/home.html", "home", "start"), 1, "screenshots"),
    "sensitive": (edit("apps/accounts/services.py", "None", "True"), 2, "sign-in"),
}


def commit(ref: str, message: str, files: Files, parent: str | None) -> bytes:
    def data(text: str) -> bytes:
        raw = text.encode("utf-8")
        return b"data %d\n" % len(raw) + raw + b"\n"

    out = f"commit {ref}\ncommitter R <r@example.com> 0 +0000\n".encode() + data(message)
    out += f"from {parent}\ndeleteall\n".encode() if parent else b""
    for path, text in sorted(files.items()):
        out += f"M 100644 inline {path}\n".encode() + data(text)
    return out


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    """One `git fast-import` builds main and every case branch: one process, not dozens."""
    root = tmp_path_factory.mktemp("review")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)  # noqa: S603, S607
    stream = commit("refs/heads/main", "base", BASE, None)
    for name, (change, _, _) in CASES.items():
        stream += commit(f"refs/heads/{name}", name, change(BASE), "refs/heads/main")
    subprocess.run(  # noqa: S603
        ["git", "fast-import", "--quiet"], cwd=root, input=stream, check=True  # noqa: S607
    )
    return root


@pytest.mark.parametrize("name", list(CASES))
def test_each_rule_fires_on_its_own_change(repo, name):
    _, expected, phrase = CASES[name]

    report = rb.review(name, "main", repo)

    found = "\n".join(report.stop + report.look)
    assert report.status == expected, found
    assert phrase in found


def test_a_stop_is_reported_once_however_many_lines_trigger_it(repo):
    report = rb.review("sensitive", "main", repo)

    assert len(report.stop) == 1


INTACT = {
    "required_pull_request_reviews": {"required_approving_review_count": 0},
    "required_status_checks": {"contexts": sorted(rb.REQUIRED_CHECKS)},
    "enforce_admins": {"enabled": True},
    "allow_force_pushes": {"enabled": False},
}


def answer(code: int = 0, body: object = INTACT, error: bytes = b""):
    def gh_api(path: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            ["gh", "api", path], code, json.dumps(body).encode(), error
        )

    return gh_api


@pytest.mark.parametrize(
    ("gh_api", "stop", "look"),
    [
        (answer(), "", ""),
        (answer(1, {}, b'{"message":"Branch not protected"}'), "no longer protected", ""),
        (answer(body=INTACT | {"required_pull_request_reviews": None}), "a pull request", ""),
        (
            answer(body=INTACT | {"required_status_checks": {"contexts": ["Lint, format, types"]}}),
            "Tests (real PostgreSQL, real Chromium)",
            "",
        ),
        (answer(body=INTACT | {"allow_force_pushes": {"enabled": True}}), "force-pushes", ""),
        (answer(1, {}, b"error connecting to api.github.com"), "", "could not be read"),
    ],
)
def test_mains_protection_is_read_from_github_before_each_merge(monkeypatch, gh_api, stop, look):
    monkeypatch.setattr(rb, "gh_api", gh_api)
    report = rb.Report()

    rb.protection(report, "main")

    assert (stop in "\n".join(report.stop)) and (bool(report.stop) == bool(stop)), report.stop
    assert (look in "\n".join(report.look)) and (bool(report.look) == bool(look)), report.look


def test_without_gh_the_protection_check_asks_for_a_look(monkeypatch):
    def missing(path: str):
        raise OSError("gh not found")

    monkeypatch.setattr(rb, "gh_api", missing)
    report = rb.Report()

    rb.protection(report, "main")

    assert report.stop == []
    assert "not installed" in report.look[0]

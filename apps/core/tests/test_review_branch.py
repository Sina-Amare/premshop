"""`scripts/review_branch.py`: each rule fires on the change it exists for, and stays quiet otherwise.

Every case is a branch off one small repository, so a rule that cannot fail shows up here
as a case that passes for the wrong reason.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
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

Files = dict[str, str | bytes]
OWNER = "R <r@example.com>"

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
    "apps/core/tests/test_written_voice.py": "def test_voice():\n    assert True\n",
    "docs/decisions/0001-stack.md": "# ADR-0001\n\nDecision.\n",
    "pyproject.toml": (
        '[project]\nname = "x"\ndependencies = ["django"]\n\n'
        '[tool.pytest.ini_options]\naddopts = "--strict-markers"\n'
    ),
    "templates/home.html": "<p>home</p>\n",
    ".github/workflows/ci.yml": "name: CI\n",
}
MIGRATION_0002 = (
    "from django.db import migrations\n\n\ndef forwards(apps, schema_editor):\n    pass\n\n\n"
    "class Migration(migrations.Migration):\n    operations = [{op}]\n"
)
TEST_TWO = "\n\ndef test_two():\n    assert True\n"


def edit(path: str, old: str, new: str) -> Callable[[Files], Files]:
    return lambda files: files | {path: str(files[path]).replace(old, new, 1)}


def add(path: str, content: str | bytes) -> Callable[[Files], Files]:
    return lambda files: files | {path: content}


def rename(old: str, new: str) -> Callable[[Files], Files]:
    return lambda files: {(new if path == old else path): body for path, body in files.items()}


def move_test_two(files: Files) -> Files:
    moved = edit("apps/shop/tests/test_views.py", TEST_TWO, "")(files)
    return moved | {"apps/shop/tests/test_other.py": "def test_two():\n    assert True\n"}


def migration(op: str) -> Callable[[Files], Files]:
    return add("apps/shop/migrations/0002_data.py", MIGRATION_0002.format(op=op))


@dataclass
class Case:
    change: Callable[[Files], Files]
    status: int
    phrase: str = ""
    message: str = ""
    author: str = OWNER


CASES = {
    "clean": Case(edit("apps/shop/views.py", "1", "2"), 0),
    "file-renamed": Case(rename("apps/shop/views.py", "apps/shop/handlers.py"), 0),
    "migration-edited": Case(
        edit("apps/shop/migrations/0001_initial.py", "[]", "[None]"), 2, "applied migration"
    ),
    "test-removed": Case(edit("apps/shop/tests/test_views.py", TEST_TWO, ""), 2, "test_two"),
    "test-moved": Case(move_test_two, 0),
    "test-renamed": Case(
        edit("apps/shop/tests/test_views.py", "def test_two", "def test_second"), 1, "renamed"
    ),
    "skip-added": Case(
        edit("apps/shop/tests/test_views.py", "def test_one", "@pytest.mark.skip\ndef test_one"),
        2,
        "skipped",
    ),
    "skip-imported": Case(
        edit("apps/shop/tests/test_views.py", "def test_one", "@mark.xfail\ndef test_one"),
        2,
        "skipped",
    ),
    "assert-weakened": Case(
        edit("apps/shop/tests/test_views.py", "    assert True", "    assert 1"), 1, "Assertions"
    ),
    "test-moved-out": Case(
        rename("apps/shop/tests/test_views.py", "scripts/tests/test_views.py"), 2, "test_one"
    ),
    "markers-quoted": Case(
        edit(
            "apps/shop/tests/test_views.py",
            "PRODUCT_PAGE",
            'NOTE = "@pytest.mark.skip and # noqa are what the rules look for"\nPRODUCT_PAGE',
        ),
        0,
    ),
    "budget-raised": Case(
        edit("apps/shop/tests/test_views.py", "= 8", "= 15"), 2, "raised from 8 to 15"
    ),
    "budget-raised-with-reason": Case(
        edit("apps/shop/tests/test_views.py", "= 8", "= 12  # the reviews block adds four"),
        2,
        "raised from 8 to 12",
    ),
    "ci-changed": Case(edit(".github/workflows/ci.yml", "CI", "CI lite"), 2, "gate itself"),
    "guard-test-changed": Case(
        edit("apps/core/tests/test_written_voice.py", "True", "1"), 2, "gate itself"
    ),
    "pytest-ini": Case(add("pytest.ini", "[pytest]\naddopts = -k 'not slow'\n"), 2, "gate"),
    "pytest-config": Case(
        edit("pyproject.toml", "--strict-markers", "-k 'not slow'"), 2, "tool.pytest.ini_options"
    ),
    "dependency": Case(
        edit("pyproject.toml", '["django"]', '["django", "requests"]'), 2, "Dependencies"
    ),
    "adr-rewritten": Case(
        edit("docs/decisions/0001-stack.md", "Decision.", "Another decision."),
        2,
        "decision record",
    ),
    "adr-amended": Case(
        edit("docs/decisions/0001-stack.md", "Decision.\n", "Decision.\n\n**Amendment.** Later.\n"),
        0,
    ),
    "secret": Case(
        edit("apps/shop/views.py", "return 1", 'return "sk-' + "x" * 24 + '"'),
        2,
        "looks like a secret",
    ),
    "env-file": Case(add("config/.env", "SECRET_KEY=x\n"), 2, "holds secrets"),
    "irreversible": Case(migration("migrations.RunPython(forwards)"), 2, "cannot be reversed"),
    "reverse-none": Case(
        migration("migrations.RunPython(forwards, None)"), 2, "cannot be reversed"
    ),
    "data-moved": Case(
        migration("migrations.RunPython(forwards, migrations.RunPython.noop)"), 2, "moves data"
    ),
    "field-altered": Case(
        migration("migrations.AlterField(model_name='p', name='x', field=None)"),
        1,
        "alters a field",
    ),
    "destructive": Case(
        migration("migrations.RemoveField(model_name='p', name='x')"), 2, "removes or reshapes"
    ),
    "silenced": Case(
        edit("apps/shop/views.py", "return 1", "return 1  # noqa: E501"), 1, "silenced"
    ),
    "template": Case(edit("templates/home.html", "home", "start"), 1, "screenshots"),
    "became-binary": Case(add("templates/home.html", b"\xff\xfe<\x00p\x00>\x00"), 2, "binary"),
    "sensitive": Case(edit("apps/accounts/services.py", "None", "True"), 2, "sign-in"),
    "new-top-level": Case(add("tools/helper.py", "X = 1\n"), 2, "top-level path"),
    "hidden-path": Case(add("apps/shop/.settings/local.json", "{}\n"), 2, "hidden path"),
    "attribution": Case(
        edit("apps/shop/views.py", "1", "3"),
        2,
        "credits someone else",
        message="views\n\nCo-authored-by: Helper <helper@example.com>\n",
    ),
    "generated-by-any-tool": Case(
        edit("apps/shop/views.py", "1", "5"),
        2,
        "credits someone else",
        message="views\n\nGenerated with a tool released next year\n",
    ),
}


def commit(ref: str, case: str, files: Files, parent: str | None, author: str = OWNER) -> bytes:
    def data(content: str | bytes) -> bytes:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        return b"data %d\n" % len(raw) + raw + b"\n"

    message = CASES[case].message if case in CASES else ""
    out = f"commit {ref}\nauthor {author} 0 +0000\ncommitter {author} 0 +0000\n".encode()
    out += data(message or case)
    out += f"from {parent}\ndeleteall\n".encode() if parent else b""
    for path, content in sorted(files.items()):
        out += f"M 100644 inline {path}\n".encode() + data(content)
    return out


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    """One `git fast-import` builds main and every case branch: one process, not dozens."""
    root = tmp_path_factory.mktemp("review")

    def git(*args: str, **kwargs) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, **kwargs)  # noqa: S603, S607

    git("init", "-q", "-b", "main")
    git("config", "user.email", "r@example.com")
    stream = commit("refs/heads/main", "base", BASE, None)
    for name, case in CASES.items():
        stream += commit(
            f"refs/heads/{name}", name, case.change(BASE), "refs/heads/main", case.author
        )
    git("fast-import", "--quiet", input=stream)
    return root


@pytest.mark.parametrize("name", list(CASES))
def test_each_rule_fires_on_its_own_change(repo, name):
    case = CASES[name]

    report = rb.review(name, "main", repo)

    found = "\n".join(report.stop + report.look)
    assert report.status == case.status, found
    assert case.phrase in found


def test_a_stop_is_reported_once_however_many_lines_trigger_it(repo):
    report = rb.review("sensitive", "main", repo)

    assert len(report.stop) == 1

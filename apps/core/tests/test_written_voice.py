"""The repository reads as work by a person.

Half of that rule is judgement and half is a word list. This is the word
list: the register markers that give away a machine draft, checked over every
file the repository tracks. The judgement half — a comment answers why rather
than what, a decision goes in an ADR rather than a twenty-line block — is not
checkable and is not excused by this passing.

`.env`, `docs-local/` and the virtualenv are not tracked, so they are not
scanned. The scan uses `git ls-files` rather than a glob for exactly that
reason: the set of files this rule applies to is the set that is published.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)

SCANNED = {".py", ".html", ".css", ".js", ".md", ".toml", ".yml", ".yaml", ".txt", ".cfg"}

# Each entry is (name, pattern). Word boundaries where the term is a word;
# the tutorial openers are anchored to a line or sentence start so that a
# legitimate "note that" inside a sentence is not caught.
BANNED = [
    (
        "brochure adjective",
        r"\b(robust|seamless|comprehensive|cutting-edge|state-of-the-art|best-in-class|leverage|utilize|delve)\b",
    ),
    (
        "tutorial voice",
        r"(?im)(^|[.!?]\s+|#\s*|\*\s*)(note that|keep in mind|as you can see|it'?s worth noting|importantly|let'?s |we'?ll |here we )",
    ),
    (
        "essay scaffolding",
        r"(?im)(^|[.!?]\s+|#\s*)(in summary|overall,|in conclusion|firstly|moreover|furthermore|additionally,)",
    ),
    (
        "non-human attribution",
        r"(?i)(as an ai\b|ai-generated"
        r"|(written|generated|authored|produced|created) by[^.\n]{0,24}(\bai\b|assistant|bot)\b)",
    ),
    # This shop has one author. A co-author trailer of any kind is therefore
    # either wrong or an attribution that does not belong in its history, and
    # a rule with no vendor list is one that cannot be outlived by a rename.
    ("co-author trailer", r"(?im)^\+?\s*co-authored-by:"),
]

# No rule above names a vendor. The shop sells ChatGPT and Claude
# subscriptions, so those words are its stock and appear all over the
# catalogue; what gives authorship away is an attribution, and an attribution
# is recognisable by its shape. A list of model names would also need editing
# every time one is renamed, which is how a ban list stops being true.

# Pictographs, and anything forced into emoji presentation by U+FE0F.
# Typographic marks are not emoji and are not what the rule is about: ✓ and ✗
# head a do/don't table in design-language.md, and ✕ names the dismiss glyph
# in a CSS comment.
EMOJI = re.compile("[\\U0001f000-\\U0001faff\\u2705\\u274c\\u2728\\u2049\\u2757]|\\ufe0f")

# This file is the ban list, so it is the one place every banned marker has to
# appear. Nothing else is exempt, and the control below pins the exemption to
# this one path so that it cannot quietly grow into a list.
SELF = Path(__file__).resolve()


def tracked_files() -> list[Path]:
    git = shutil.which("git")
    assert git, "this test reads the tracked file list from git"
    out = subprocess.run(  # noqa: S603 - argv is literal, the path is resolved above
        [git, "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    # `is_file()` because git lists what is tracked, not what is on disk: a
    # file deleted but not yet staged is still in the index, and reading it
    # crashed the scan. A lint that errors on an ordinary working state
    # teaches people to skip it.
    found = [
        ROOT / n
        for n in out.split("\0")
        if n and Path(n).suffix in SCANNED and (ROOT / n).is_file()
    ]
    return [path for path in found if path.resolve() != SELF]


def offences(text: str) -> list[str]:
    """Every banned marker in one file's text."""
    found = [
        f"{name}: {m.group(0).strip()!r}" for name, pat in BANNED for m in re.finditer(pat, text)
    ]
    found += [f"emoji: {m.group(0)!r}" for m in EMOJI.finditer(text)]
    return found


def test_the_file_list_is_not_empty():
    """A scan over nothing passes. This is the positive control for the walk."""
    files = tracked_files()
    assert len(files) > 50, files
    assert any(f.name == "models.py" for f in files)
    assert SELF.exists() and SELF not in files, "it excludes itself, and only itself"


def test_no_tracked_file_carries_a_machine_register_marker():
    bad = {}
    for path in tracked_files():
        try:
            found = offences(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            # Every scanned suffix is text, so this is damage, such as a Persian file
            # rewritten as UTF-16 by an editor or a shell; skipping it hid the damage.
            found = ["not UTF-8"]
        if found:
            bad[path.relative_to(ROOT).as_posix()] = found
    assert not bad, f"machine register in tracked files: {bad}"


@pytest.mark.parametrize(
    "text",
    [
        "# This is a robust solution to the problem.",
        "# Note that the cache is cleared on logout.",
        "# In summary, the three rules above hold.",
        "# Generated by an assistant, 2026-10-13.",
        "# Written by AI and reviewed later.",
        "Co-Authored-By: a coding assistant <noreply@example.test>",
        "# ships it \U0001f680",
        "# we'll come back to this",
    ],
)
def test_the_scan_catches_what_it_is_meant_to_catch(text):
    """Without this the suite could not tell a clean repository from a blind
    regex. Each line is a shape that has really appeared in generated code."""
    assert offences(text), text


@pytest.mark.parametrize(
    "text",
    [
        "# One query, or two when a department is too thin to fill the row.",
        "# The gateway is asked again; a callback's word is never enough.",
        '"""Products with everything a card needs, in two queries however many rows."""',
        "name='Claude Pro', slug='claude-pro'",
        "# Generated by Django 5.2.17 on 2026-09-04 20:31",
        "ایمیل حساب Claude را می‌پرسیم.",
    ],
)
def test_the_scan_leaves_ordinary_writing_alone(text):
    assert not offences(text), text

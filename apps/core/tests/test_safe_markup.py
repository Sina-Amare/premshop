"""Nothing reaches a page unescaped except through two reviewed functions.

A category intro printed with `|safe` let anyone who could edit a category
put a script on a page where customers pay. Django escapes every variable by
default; each way around that is banned here, and `mark_safe` is allowed in
exactly two files: the constant zero-price markup in `fa.py`, and the
restricted-Markdown renderer in `richtext.py`, which sanitises what it emits.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SELF = Path(__file__).resolve()

TEMPLATE_BYPASS = re.compile(r"\|\s*safe(seq)?\b|{%\s*autoescape\s+off")
CODE_BYPASS = re.compile(r"\bmark_safe\b|\bSafe(String|Text)\(|\bhtml_safe\b|def __html__")
ALLOWED = {"apps/core/templatetags/fa.py", "apps/core/richtext.py"}
# A template comment may name the filter it avoids; only what renders counts.
# `{# #}` is a comment only within one line, as Django lexes it.
TEMPLATE_COMMENT = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#[^\n]*?#}", re.S)


def templates() -> list[Path]:
    # Every file, not a list of suffixes: the .svg partials are templates too.
    return [p for p in (ROOT / "templates").rglob("*") if p.is_file()]


def python_files() -> list[Path]:
    found = [p for top in ("apps", "config", "scripts") for p in (ROOT / top).rglob("*.py")]
    return [p for p in found if p.resolve() != SELF and "migrations" not in p.parts]


def lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        # Blanked rather than removed, so line numbers still point somewhere.
        text = TEMPLATE_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return text.splitlines()


def offenders(paths: list[Path], pattern: re.Pattern[str]) -> list[str]:
    return [
        f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()}"
        for path in paths
        for number, line in enumerate(lines(path), 1)
        if pattern.search(line)
    ]


def test_the_scan_walks_real_files():
    """A scan over nothing passes. These are the controls for both walks."""
    assert any(p.name == "category.html" for p in templates())
    assert any(p.as_posix().endswith("svg/hero.svg") for p in templates())
    assert any(p.as_posix().endswith("apps/core/templatetags/fa.py") for p in python_files())


def test_no_template_switches_escaping_off():
    assert not offenders(templates(), TEMPLATE_BYPASS)


def test_mark_safe_lives_only_where_it_was_reviewed():
    bad = [
        line
        for line in offenders(python_files(), CODE_BYPASS)
        if line.split(":", 1)[0] not in ALLOWED
    ]
    assert not bad


@pytest.mark.parametrize(
    "line",
    [
        "{{ category.intro_html|safe }}",
        "{{ items | safeseq }}",
        "{% autoescape off %}{{ x }}{% endautoescape %}",
    ],
)
def test_the_template_pattern_catches_each_bypass(line):
    assert TEMPLATE_BYPASS.search(line), line


@pytest.mark.parametrize(
    "line",
    [
        "from django.utils.safestring import mark_safe",
        "return mark_safe(f'<b>{name}</b>')",
        "return SafeString(text)",
        "from django.utils.html import html_safe",
        "@html_safe",
        "    def __html__(self) -> str:",
    ],
)
def test_the_code_pattern_catches_each_bypass(line):
    assert CODE_BYPASS.search(line), line


@pytest.mark.parametrize(
    "line",
    [
        "{{ product.name }}",
        "{{ product.full_description|linebreaks }}",
        "def render(self) -> SafeString:",
        "return format_html('<b>{}</b>', name)",
    ],
)
def test_the_patterns_leave_escaped_output_alone(line):
    assert not TEMPLATE_BYPASS.search(line)
    assert not CODE_BYPASS.search(line)


def test_a_comment_naming_the_filter_is_not_a_use_of_it(tmp_path):
    """Django reads `{# #}` as a comment only within one line. Across lines
    it is text, and what sits between the two marks renders."""
    page = tmp_path / "page.html"
    page.write_text(
        "{% comment %}Escaped, unlike `|safe`.{% endcomment %}\n{# not |safe #}\n{{ x|safe }}\n"
        "{# a note\n{{ y|safe }}\n#}\n",
        encoding="utf-8",
    )

    assert [line for line in lines(page) if TEMPLATE_BYPASS.search(line)] == [
        "{{ x|safe }}",
        "{{ y|safe }}",
    ]

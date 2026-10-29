"""The panel's two catalogues: compiled as written, and complete for English.

The Persian catalogue's header promised a test that the .mo on disk matches
the .po, and there was none: a forgotten compile would have shipped stale
wording with nothing failing. The English one is what makes the language
switch turn the whole panel English, so a Persian label added without its
English would put Persian back on the English side, silently.
"""

from __future__ import annotations

import ast
import gettext
import importlib.util
import re
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
PERSIAN = re.compile(r"[\u0600-\u06ff]")
PANEL_CODE = [
    "apps/catalog/models.py",
    "apps/catalog/admin.py",
    "apps/catalog/admin_preview.py",
    "apps/catalog/placements.py",
    "apps/accounts/admin.py",
    "apps/cms/apps.py",
    "apps/cms/models.py",
    "apps/cms/admin.py",
    # The apps' names in the path, and the sidebar's titles.
    "apps/catalog/apps.py",
    "apps/accounts/apps.py",
    "apps/orders/apps.py",
    "apps/orders/models.py",
    "apps/orders/admin.py",
    "config/settings/base.py",
]
PANEL_TEMPLATES = ["templates/admin", "templates/unfold"]


def compile_messages():
    spec = importlib.util.spec_from_file_location(
        "compile_messages", ROOT / "scripts" / "compile_messages.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def persian_msgids() -> set[str]:
    """Every Persian string the panel's own code and templates translate."""
    found = set()
    for rel in PANEL_CODE:
        for node in ast.walk(ast.parse((ROOT / rel).read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"_", "gettext", "gettext_lazy"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
    for folder in PANEL_TEMPLATES:
        for path in (ROOT / folder).rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            found.update(re.findall(r'{%\s*(?:translate|trans)\s+"([^"]+)"', text))
            for body in re.findall(
                r"{%\s*blocktranslate[^%]*%}(.*?){%\s*endblocktranslate", text, re.S
            ):
                found.add(re.sub(r"{{\s*(\w+)\s*}}", r"%(\1)s", body))
    return {msgid for msgid in found if PERSIAN.search(msgid)}


@pytest.mark.parametrize("language", ["fa", "en"])
def test_the_compiled_catalogue_is_the_written_one(language, tmp_path):
    script = compile_messages()
    po = ROOT / "locale" / language / "LC_MESSAGES" / "django.po"
    fresh = tmp_path / "django.mo"

    script.write_mo(script.parse_po(po), fresh)

    assert (
        fresh.read_bytes() == po.with_suffix(".mo").read_bytes()
    ), f"run scripts/compile_messages.py: {po.with_suffix('.mo')} is stale"


def test_the_compiler_reads_plurals_quotes_and_contexts(tmp_path):
    """The first compiler read plain pairs only and dropped the rest without a
    word, so a plural entry was missing from the .mo while the test above,
    compiling the same way, agreed with it."""
    po = tmp_path / "django.po"
    po.write_text(
        'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "%(n)s result"\nmsgid_plural "%(n)s results"\n'
        'msgstr[0] "%(n)s نتیجه"\nmsgstr[1] "%(n)s نتیجه"\n\n'
        'msgid ""\n"Say \\"hi\\"\\n"\n"  twice"\nmsgstr "دو بار بگو «سلام»"\n\n'
        'msgctxt "month"\nmsgid "May"\nmsgstr "مه"\n\n'
        '#, fuzzy\nmsgid "Draft"\nmsgstr "پیش‌نویس"\n',
        encoding="utf-8",
    )
    mo = tmp_path / "django.mo"
    script = compile_messages()

    script.write_mo(script.parse_po(po), mo)

    with mo.open("rb") as handle:
        catalogue = gettext.GNUTranslations(handle)
    assert catalogue.ngettext("%(n)s result", "%(n)s results", 3) == "%(n)s نتیجه"
    assert catalogue.gettext('Say "hi"\n  twice') == "دو بار بگو «سلام»"
    assert catalogue.pgettext("month", "May") == "مه"
    assert catalogue.gettext("Draft") == "Draft", "a fuzzy entry is not a translation"


@pytest.mark.parametrize(
    "po",
    [
        'msgid "a"\nmsgstr "b"\nmsgstr_typo "c"\n',
        'msgid "a"\nmsgid_plural "as"\nmsgstr[0] "b"\nmsgstr[1] ""\n',
    ],
)
def test_the_compiler_refuses_what_it_cannot_read(tmp_path, po):
    path = tmp_path / "django.po"
    path.write_text(po, encoding="utf-8")

    with pytest.raises(ValueError):
        compile_messages().parse_po(path)


def test_every_persian_string_in_the_panel_has_its_english():
    english = compile_messages().parse_po(ROOT / "locale" / "en" / "LC_MESSAGES" / "django.po")
    wanted = persian_msgids()

    assert len(wanted) > 80 and "نامک" in wanted, "positive control: the walk found the labels"
    missing = sorted(wanted - english.keys())
    assert not missing, f"add these to locale/en/LC_MESSAGES/django.po: {missing}"


def test_the_english_never_carries_a_persian_word_of_its_own():
    """Quoted Persian is allowed: the aliases hint shows what customers type,
    and the delivery hints quote the sentence the shop prints."""
    english = compile_messages().parse_po(ROOT / "locale" / "en" / "LC_MESSAGES" / "django.po")
    quoted = ("چت جی‌پی‌تی", "را می‌پرسیم", "تا … ساعت")

    leaks = [
        f"{msgid} -> {msgstr}"
        for msgid, msgstr in english.items()
        if msgid and PERSIAN.search(msgstr) and not any(q in msgstr for q in quoted)
    ]
    assert not leaks, leaks

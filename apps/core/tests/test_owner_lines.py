"""The table of the owner's rewrites matches the shipped copy in both directions.

The table in docs/design-language.md («Lines the owner rewrote») is the model other copy
imitates. Each owner line must still be shipped, and the line it replaced must not be: a row
whose line left the code teaches wording the shop no longer uses, and a returned line silently
overrides the owner. Changing one is the owner's decision and updates its row.
"""

import ast
import re
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)

# Owner lines shipped in another form, mapped to the source text that ships them: a rendered
# value is checked against its template, and the Telegram line is conditional because the request
# is queued and the site cannot yet know the code reached the bot (the table's row says so).
SHIPPED_AS = {
    "۴ از ۵ · ۱ نظر": "{{ review_average|fa_digits }} از ۵ · {{ product_reviews|length|fa_digits }} نظر",
    "کد به ربات پرم‌شاپ در حساب تلگرام شما فرستاده شد.": (
        "اگر حساب تلگرامتان به حساب کاربری متصل باشد، کد ورود را در ربات پرم‌شاپ می‌بینید."
    ),
}
# A one-word line is checked in the element that shows it, since the word occurs elsewhere.
IN_CONTEXT = {"خریدار": "review__buyer"}


def quoted(cell: str) -> str:
    """The first «…» in a cell, nested guillemets included; the whole cell when it has none."""
    start = cell.find("«")
    if start < 0:
        return cell.strip()
    depth = 0
    for i in range(start, len(cell)):
        depth += {"«": 1, "»": -1}.get(cell[i], 0)
        if depth == 0:
            return cell[start + 1 : i]
    raise AssertionError(f"unbalanced guillemets: {cell}")


def table() -> list[tuple[str, str]]:
    doc = (ROOT / "docs" / "design-language.md").read_text(encoding="utf-8")
    section = doc.split("### Lines the owner rewrote", 1)[1].split("\n### ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")][2:]
    return [(quoted(row.split(" | ")[0][2:]), quoted(row.split(" | ")[1])) for row in rows]


def template_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\{% comment %\}.*?\{% endcomment %\}|\{#.*?#\}", " ", text, flags=re.S)
    return re.sub(r"<[^>]+>", "", text)


def python_strings(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def shipped_text() -> str:
    texts = [
        template_text(p) for p in (ROOT / "templates").rglob("*") if p.suffix in (".html", ".txt")
    ]
    texts += [
        python_strings(p)
        for p in (ROOT / "apps").rglob("*.py")
        if "tests" not in p.parts and "migrations" not in p.parts
    ]
    texts += [p.read_text(encoding="utf-8") for p in (ROOT / "static" / "js").glob("*.js")]
    return re.sub(r"\s+", " ", "\n".join(texts))


def test_every_row_of_the_table_is_read() -> None:
    rows = table()
    assert len(rows) > 15
    assert all(rejected and owner for rejected, owner in rows)


def test_every_owner_line_is_shipped() -> None:
    shipped = shipped_text()
    missing = [
        owner
        for _, owner in table()
        if owner not in IN_CONTEXT and SHIPPED_AS.get(owner, owner) not in shipped
    ]
    assert missing == []


def test_a_one_word_owner_line_is_shipped_in_its_element() -> None:
    templates = " ".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "templates").rglob("*.html")
    )
    for word, css_class in IN_CONTEXT.items():
        element = 'class="' + css_class + r'"[^>]*>(?:\s*\{%[^%]*%\})*\s*' + word + r"\s*<"
        assert re.search(element, templates), word


def test_no_rejected_line_is_shipped() -> None:
    shipped = shipped_text()
    assert [rejected for rejected, _ in table() if rejected in shipped] == []

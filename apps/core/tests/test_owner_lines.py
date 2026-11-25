"""Every line in the Voice section's table of the owner's rewrites is still in the shipped code.

The table is the model other copy imitates (docs/design-language.md, «Lines the owner rewrote»).
A line that drifts from the code teaches wording the shop no longer uses, and a silent edit
overrides the owner, so changing one means asking the owner and updating the table.
"""

import re
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
DIGITS = re.compile(r"\{\{[^}]*\}\}|[۰-۹]+")

# The shipped line is conditional because the Telegram request is queued and the site cannot yet
# know the code reached the bot; the table's row says so.
SHIPPED_DIFFERENTLY = {"کد به ربات پرم‌شاپ در حساب تلگرام شما فرستاده شد."}


def owner_lines() -> list[str]:
    doc = (ROOT / "docs" / "design-language.md").read_text(encoding="utf-8")
    table = doc.split("### Lines the owner rewrote", 1)[1].split("\n### ", 1)[0]
    lines = []
    for row in table.splitlines():
        if row.startswith("| «"):
            cell = row.split(" | ")[1]
            quoted = re.match(r"«(.+?)»(?=[,.\s]|$)", cell)
            lines.append(quoted.group(1) if quoted else cell)
    return lines


def shipped_text() -> str:
    paths = list((ROOT / "templates").rglob("*.html")) + list((ROOT / "templates").rglob("*.txt"))
    paths += [p for p in (ROOT / "apps").rglob("*.py") if "tests" not in p.parts]
    texts = [re.sub(r"<[^>]+>", "", p.read_text(encoding="utf-8")) for p in paths]
    return re.sub(r"\s+", " ", "\n".join(texts))


def test_the_table_is_read() -> None:
    assert len(owner_lines()) > 15


def test_every_owner_line_is_in_the_code() -> None:
    shipped = shipped_text()
    # A rendered value, such as «۴ از ۵ · ۱ نظر», matches its template with digits and variables masked.
    masked = DIGITS.sub("#", shipped)
    missing = [
        line
        for line in owner_lines()
        if line not in SHIPPED_DIFFERENTLY
        and line not in shipped
        and DIGITS.sub("#", line) not in masked
    ]
    assert missing == []

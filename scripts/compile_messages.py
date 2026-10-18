"""Compile locale/*.po into the .mo files Django reads, without gettext.

`manage.py compilemessages` shells out to GNU msgfmt, which is not installed on
this machine and is not something to make a new developer install before the
admin reads correctly in Persian. The .mo container is a header and two string
tables; writing it here is less code than a build dependency.

    .venv/Scripts/python.exe scripts/compile_messages.py

Run it after editing a .po. A test asserts the .mo on disk matches the .po, so
a forgotten run fails the gate rather than silently shipping stale Persian.
"""

from __future__ import annotations

import array
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCALE = REPO / "locale"

# A .po entry is a msgid/msgstr pair, each possibly continued over several
# quoted lines. Only what this project writes is parsed: no plural forms, no
# contexts, no fuzzy flags. Anything else is a silent no-op, so the test that
# compares .po to .mo is what keeps this honest.
ENTRY = re.compile(
    r'^msgid\s+((?:"[^"]*"\s*)+)^msgstr\s+((?:"[^"]*"\s*)+)', re.MULTILINE | re.DOTALL
)


def _unquote(block: str) -> str:
    return "".join(re.findall(r'"([^"]*)"', block)).replace("\\n", "\n").replace('\\"', '"')


def parse_po(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    catalog = {}
    for msgid, msgstr in ENTRY.findall(text):
        key, value = _unquote(msgid), _unquote(msgstr)
        # An empty msgstr means "not translated"; gettext falls through to the
        # msgid, and so must we — shipping "" would blank the label.
        if value or key == "":
            catalog[key] = value
    return catalog


def write_mo(catalog: dict[str, str], path: Path) -> None:
    # The metadata entry is not optional: without a Content-Type naming UTF-8,
    # Python's gettext decodes the catalogue as ASCII and every Persian string
    # raises UnicodeDecodeError at read time rather than at build time.
    catalog = dict(catalog)
    catalog.setdefault("", "Content-Type: text/plain; charset=UTF-8\n")

    keys = sorted(catalog)
    offsets, ids, strs = [], b"", b""
    for key in keys:
        encoded_key, encoded_value = key.encode(), catalog[key].encode()
        offsets.append((len(ids), len(encoded_key), len(strs), len(encoded_value)))
        ids += encoded_key + b"\x00"
        strs += encoded_value + b"\x00"

    key_start = 7 * 4 + 16 * len(keys)
    value_start = key_start + len(ids)
    key_offsets, value_offsets = [], []
    for id_offset, id_length, str_offset, str_length in offsets:
        key_offsets += [id_length, id_offset + key_start]
        value_offsets += [str_length, str_offset + value_start]

    path.write_bytes(
        struct.pack("Iiiiiii", 0x950412DE, 0, len(keys), 7 * 4, 7 * 4 + len(keys) * 8, 0, 0)
        + array.array("i", key_offsets + value_offsets).tobytes()
        + ids
        + strs
    )


def main() -> int:
    found = sorted(LOCALE.glob("*/LC_MESSAGES/*.po"))
    if not found:
        print(f"no .po files under {LOCALE}", file=sys.stderr)
        return 1
    for po in found:
        catalog = parse_po(po)
        mo = po.with_suffix(".mo")
        write_mo(catalog, mo)
        print(f"{po.relative_to(REPO)} -> {mo.name} ({len(catalog) - 1} strings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

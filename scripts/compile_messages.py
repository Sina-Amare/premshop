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

# One keyword and one quoted string per line, continued by bare quoted lines.
# The first version read only msgid/msgstr pairs with no quote inside them and
# skipped anything else without a word, so a plural entry vanished from the .mo
# while the test that compiles the .po again agreed with it. A line this cannot
# read is now an error.
KEYWORD = re.compile(r'^(msgctxt|msgid|msgid_plural|msgstr(?:\[\d+\])?)\s+(".*")$')
ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _unquote(quoted: str) -> str:
    return re.sub(r"\\(.)", lambda m: ESCAPES[m.group(1)], quoted[1:-1])


def _key_and_value(entry: dict[str, str]) -> tuple[str, str | None]:
    """An entry as the .mo stores it: a plural's two forms and its translations
    are each joined by NUL, and a context goes before the key with EOT."""
    key = entry["msgid"]
    if "msgid_plural" in entry:
        key += "\x00" + entry["msgid_plural"]
        count = sum(name.startswith("msgstr[") for name in entry)
        forms = [entry[f"msgstr[{n}]"] for n in range(count)]
        if not forms or (any(forms) and not all(forms)):
            raise ValueError(f"{key!r}: a plural needs every form translated, or none")
        value = "\x00".join(forms) if all(forms) else None
    else:
        # An empty msgstr means "not translated"; gettext falls through to the
        # msgid, and so must we. Shipping "" would blank the label.
        value = entry["msgstr"] if entry["msgstr"] or not key else None
    if "msgctxt" in entry:
        key = entry["msgctxt"] + "\x04" + key
    return key, value


def parse_po(path: Path) -> dict[str, str]:
    catalog: dict[str, str] = {}
    entry: dict[str, str] = {}
    field, fuzzy = "", False

    def close() -> None:
        nonlocal entry, field, fuzzy
        if entry and not fuzzy:  # msgfmt leaves fuzzy entries out as well
            key, value = _key_and_value(entry)
            if value is not None:
                catalog[key] = value
        entry, field, fuzzy = {}, "", False

    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        translated = any(name.startswith("msgstr") for name in entry)
        if not line or line.startswith("#"):
            if translated:
                close()
            fuzzy = fuzzy or (line.startswith("#,") and "fuzzy" in line)
        elif match := KEYWORD.match(line):
            if translated and match.group(1) in {"msgctxt", "msgid"}:
                close()
            field = match.group(1)
            entry[field] = _unquote(match.group(2))
        elif field and line.startswith('"') and line.endswith('"'):
            entry[field] += _unquote(line)
        else:
            raise ValueError(f"{path.name}:{number}: cannot read {raw!r}")
    close()
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

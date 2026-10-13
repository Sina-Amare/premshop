"""Cut the web fonts down to the characters this shop actually renders.

Fonts are 81% of the bytes on the home page, and the shipped faces carry the
whole Arabic block plus Latin Extended for a UI that uses a couple of hundred
codepoints. Subsetting roughly halves them, which matters more here than
anywhere else in the codebase: the customers are on Iranian mobile links.

Not part of the build. Run it by hand when a face is replaced, read the
numbers it prints, and commit the result — a font that is regenerated on every
deploy is a font nobody has looked at.

    .venv/Scripts/python.exe -m pip install fonttools brotli
    .venv/Scripts/python.exe scripts/subset_fonts.py

What must survive the cut, and why:
  * the Arabic block, plus the four Persian letters that live outside it and
    the ZWNJ that holds compound words together;
  * `locl`, which both faces use to select Persian shapes for ک/ی/۴/۶/۷ —
    `templates/base.html` explains why the page declares lang="fa" at all;
  * the joining features (init/medi/fina/isol) and the mark positioning
    (mark/mkmk), without which Arabic script stops being cursive;
  * basic Latin, because every product on the shelf is a Latin name inside a
    Persian sentence, and a Netflix that falls back to Tahoma mid-line is the
    exact "assembled from parts" look this redesign is fixing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FONTS = Path(__file__).resolve().parent.parent / "static" / "fonts"

UNICODES = ",".join(
    [
        "U+0020-007E",  # basic Latin: brand names, emails, URLs
        "U+00A0",
        "U+0600-06FF",  # Arabic block, which carries Persian
        "U+067E,U+0686,U+0698,U+06AF",  # پ چ ژ گ, for fonts that place them outside
        "U+200C-200F",  # ZWNJ and the bidi marks
        "U+2010-2015,U+2018-201F,U+2026",  # dashes, quotes, ellipsis
        "U+00AB,U+00BB",  # « »
        "U+066A-066C",  # Arabic percent and separators
        "U+FB8A",  # ژ presentation form
    ]
)

FEATURES = ",".join(
    [
        "locl",
        "init",
        "medi",
        "fina",
        "isol",
        "rlig",
        "liga",
        "calt",
        "mark",
        "mkmk",
        "ccmp",
        "kern",
        "tnum",
        "ss01",
    ]
)


def main() -> int:
    total_before = total_after = 0
    for source in sorted(FONTS.glob("*.woff2")):
        if source.stem.endswith("-subset"):
            continue
        before = source.stat().st_size
        subprocess.run(  # noqa: S603 — fixed argv, no shell, paths from this file
            [
                sys.executable,
                "-m",
                "fontTools.subset",
                str(source),
                f"--unicodes={UNICODES}",
                f"--layout-features+={FEATURES}",
                "--flavor=woff2",
                "--no-hinting",
                "--desubroutinize",
                f"--output-file={source}",
            ],
            check=True,
        )
        after = source.stat().st_size
        total_before += before
        total_after += after
        print(f"{source.name:30} {before:>7} -> {after:>7}  ({after / before - 1:+.0%})")
    print(
        f"{'TOTAL':30} {total_before:>7} -> {total_after:>7}  ({total_after / total_before - 1:+.0%})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

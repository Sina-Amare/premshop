"""Fold Persian text into one canonical spelling, so equivalent inputs match.

The same idea as lowercasing an email address before comparing it: the customer
and the operator will type the same word differently — an Arabic keyboard gives
ي and ك where Persian has ی and ک, phones put a space where a half-space
belongs, and digits arrive in three scripts. Store the folded form once on save,
fold the query the same way, and «كلاود» finds «کلاود».

Folding is lossy on purpose. It is for MATCHING, never for display.
"""

from __future__ import annotations

import re
import unicodedata

# Arabic-script look-alikes an Iranian keyboard or a pasted text can carry.
_CHAR_FOLD = str.maketrans(
    {
        "ي": "ی",  # ي arabic yeh → ی persian yeh
        "ى": "ی",  # ى alef maksura → ی
        "ك": "ک",  # ك arabic kaf → ک persian kaf
        "ۀ": "ه",  # ۀ heh with yeh above → ه
        "ة": "ه",  # ة teh marbuta → ه
        "أ": "ا",  # أ → ا
        "إ": "ا",  # إ → ا
        "آ": "ا",  # آ → ا
        "ؤ": "و",  # ؤ → و
        "ـ": "",  # ـ tatweel: stretching, never meaning
        "‌": " ",  # half-space → space: what phones type instead of it
        "‍": "",
        "‎": "",
        "‏": "",
    }
)

_DIGIT_FOLD = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Harakat and other combining marks: کِتاب and کتاب are the same word.
_MARKS = re.compile(r"[ً-ْٰٓ-ٕ]")
_SPACES = re.compile(r"\s+")


def normalize_for_search(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text or "")
    folded = _MARKS.sub("", folded)
    folded = folded.translate(_CHAR_FOLD).translate(_DIGIT_FOLD)
    folded = folded.lower()
    return _SPACES.sub(" ", folded).strip()

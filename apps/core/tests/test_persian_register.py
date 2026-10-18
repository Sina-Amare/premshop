"""The Persian the operator reads is held to the same rules as the shop's.

`docs/design-language.md` §Voice governs customer copy. An admin panel is a
different register — it is all labels, hints and errors, which §Voice already
exempts from the contracted colloquial style — but the bans survive, and a few
more apply that only matter when the reader is the owner.

The rules here are the regex-checkable half of a measured set: 22,859 strings
of Persian WooCommerce/WordPress admin, Django's own fa catalogue, and eleven
Iranian commercial products. Two numbers decided most of it. Of 183 real
action labels in that corpus, 2 carry a «ـید» imperative — a button is a bare
verbal noun. And the commercial market uses «نمایید» four times where
WordPress uses it 604, which is why WordPress fa is a vocabulary source and an
anti-model for register.

What is deliberately NOT checked here, because it needs a reader: whether a
sentence states the consequence rather than the definition, and whether an
example is a real value. Those are the half that matters most.
"""

from __future__ import annotations

import re

import pytest
from django.apps import apps

# Each entry: the marker, and what to write instead. The message is the point —
# a failure that only says "banned" teaches nobody.
BANNED = [
    (r"می‌باشد", "«است», or drop the copula"),
    (r"نمایید|فرمایید", "«کنید»"),
    (r"می‌گردد(?<!بر می‌گردد)|گردید\b", "«می‌شود» / «شد»"),
    (r"(?<![\w‌])جهت(?= )", "«برای»"),
    (r"توسط", "active voice: name who did it"),
    (r"انجام می‌پذیرد", "a plain verb"),
    (r"گزارشات|پیشنهادات|سفارشات|آزمایشات", "a Persian plural in «ها»"),
    (r"لطفاً", "nothing — the panel talks to its owner"),
    (r"متأسفانه", "the cause instead of an apology"),
    (r"شیء|اشیاء", "«مورد» — «object» is a programming word"),
    (r"وب‌گاه", "«سایت» / «پنل»"),
    (r"گذرواژه", "«رمز عبور», which this shop has said since S2"),
    (r"بانک اطلاعاتی", "«پایگاه داده»"),
    # §Voice bans the em dash in body copy; two help strings had slipped.
    (r"—", "«:» or a full stop"),
    # ZWNJ, the mistake that makes Persian look machine-set.
    (r"(?<![\w‌])می [اآبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی]", "a half-space: «می‌…»"),
]

# Latin digits inside a Persian sentence. Identifiers, codes and format
# placeholders are exempt: this is about quantities a person reads.
#
# `[0-9]` and not `\d`. Python's `\d` is Unicode-aware and matches ۰-۹ as
# happily as 0-9, so the first version of this flagged «بین ۱ تا ۷۲ ساعت» as
# Latin digits. The negative control below is what caught it, which is the
# whole reason a lint ships with examples of what it must leave alone.
LATIN_DIGIT_IN_PERSIAN = re.compile(r"[؀-ۿ][^\n]{0,20}?(?<![%\w(])[0-9]+(?![\w)%])")

MAX_WORDS = 18  # the corpus median is 12; p90 is 27 and reads as a paragraph


def model_strings() -> list[tuple[str, str, str]]:
    """Every label and hint an operator can read, with where it came from."""
    out = []
    for model in apps.get_models():
        if not model.__module__.startswith("apps."):
            continue
        for field in model._meta.get_fields():
            if not hasattr(field, "help_text") or getattr(field, "auto_created", False):
                continue
            # Inherited from PermissionsMixin, carrying Django's own wording,
            # and absent from every form in this project — UserAdmin does not
            # list them. Holding Django's text to this project's rules would
            # fail on strings nobody can read.
            if field.name in {"is_superuser", "groups", "user_permissions", "password"}:
                continue
            where = f"{model.__name__}.{field.name}"
            if field.help_text:
                out.append((where, "help_text", str(field.help_text)))
            verbose = str(getattr(field, "verbose_name", "") or "")
            if verbose:
                out.append((where, "verbose_name", verbose))
    return out


def test_there_are_strings_to_check():
    """A scan over nothing passes. This is the control for the walk."""
    found = model_strings()
    assert len(found) > 60, found
    assert any(t == "help_text" for _, t, _ in found)


@pytest.mark.parametrize("pattern, instead", BANNED)
def test_no_model_string_uses_a_banned_form(pattern, instead):
    bad = [
        f"{where}.{kind}: {text}"
        for where, kind, text in model_strings()
        if re.search(pattern, text)
    ]
    assert not bad, f"write {instead} — {bad}"


def test_help_text_stays_one_short_sentence():
    """Measured, not felt: real Persian admin help runs to a median of 12
    words and this project's own strings matched it before the cap existed.
    Past about eighteen it stops being a hint and becomes a paragraph the
    operator skips."""
    long = [
        (where, len(text.split()), text)
        for where, kind, text in model_strings()
        if kind == "help_text" and len(text.split()) > MAX_WORDS
    ]
    assert not long, f"over {MAX_WORDS} words: {long}"


def test_quantities_are_written_in_persian_digits():
    """Persian digits for anything read as a quantity. The exceptions are
    identifiers and format placeholders, which the pattern already skips."""
    bad = [
        f"{where}.{kind}: {text}"
        for where, kind, text in model_strings()
        if LATIN_DIGIT_IN_PERSIAN.search(text)
    ]
    assert not bad, bad


@pytest.mark.parametrize(
    "text, why",
    [
        ("این فیلد اجباری می‌باشد.", "copula"),
        ("ذخیره نمایید", "نمایید"),
        ("سفارش ثبت گردید.", "گردید"),
        ("جهت ویرایش کلیک کنید", "جهت"),
        ("این سفارش توسط اپراتور لغو شد.", "توسط"),
        ("لطفاً دوباره تلاش کنید.", "لطفاً"),
        ("این شیء تاریخچه ندارد.", "شیء"),
        ("گذرواژه خود را بنویسید", "گذرواژه"),
        ("خالی بگذارید — صفر قبول نمی‌شود.", "em dash"),
        ("این کار انجام می پذیرد", "missing half-space"),
    ],
)
def test_the_lint_catches_what_it_is_meant_to_catch(text, why):
    """Without these the suite cannot tell clean Persian from a blind regex.
    Every line is a form that really appears in Django's or WordPress's fa."""
    assert any(re.search(p, text) for p, _ in BANNED), why


@pytest.mark.parametrize(
    "text",
    [
        "عدد کوچک‌تر بالاتر می‌آید.",
        "خالی بگذارید تا از روی اسم ساخته شود.",
        "مثل: ChatGPT Plus",
        "چقدر برای خودتان آب می‌خورد.",
        "تیک را بردارید تا خریدنی نباشد.",
        "بین ۱ تا ۷۲ ساعت.",
    ],
)
def test_the_lint_leaves_good_persian_alone(text):
    """Colloquial lexis is not the target. «آب می‌خورد» and «توی» are the
    strongest signal that a person wrote this; only the bookish forms go."""
    assert not any(re.search(p, text) for p, _ in BANNED), text
    assert not LATIN_DIGIT_IN_PERSIAN.search(text), text

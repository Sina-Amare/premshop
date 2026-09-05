"""The one test that matters for search: an Iranian keyboard finds the product."""

from __future__ import annotations

import pytest

from apps.catalog.search import normalize_for_search


@pytest.mark.parametrize(
    "typed, canonical",
    [
        ("كلاود", "کلاود"),  # arabic kaf ك → persian ک
        ("جيميل", "جیمیل"),  # arabic yeh ي → persian ی
        ("موسیقى", "موسیقی"),  # alef maksura ى → ی
        ("نرم‌افزار", "نرم افزار"),  # half-space → space: what phones type instead
        ("نرم افزار", "نرم افزار"),
        ("۱۲ ماهه", "12 ماهه"),  # persian digits → ascii
        ("١٢ ماهه", "12 ماهه"),  # arabic-indic digits → ascii
        ("Claude Pro", "claude pro"),  # latin case folded
        ("کِتاب", "کتاب"),  # harakat removed
        ("کــلاود", "کلاود"),  # tatweel removed
        ("  کلاود   پرو ", "کلاود پرو"),  # whitespace collapsed
        ("", ""),
    ],
)
def test_search_normalization_yeh_kaf_halfspace_digits(typed, canonical):
    assert normalize_for_search(typed) == canonical


def test_normalization_is_idempotent():
    once = normalize_for_search("كلاود ١٢ ماهه نرم‌افزار")
    assert normalize_for_search(once) == once


def test_folding_is_for_matching_never_for_display():
    """The folded form loses the half-space; it must never be shown to anyone."""
    from apps.catalog.models import Product

    assert Product._meta.get_field("search_text").editable is False

"""One class must not live in both cascade orders.

`static/css/storefront.css` is unlayered, so it beats anything `input.css` puts in
an `@layer`, whatever the specificity. The names below are overridden on purpose.
A name that lands in both files by accident gets the unlayered copy silently; a name
that leaves one file is a rule that stopped overriding. Either way the list changes
on purpose, in the same commit.
"""

import re
from pathlib import Path

from django.conf import settings

CSS = Path(settings.BASE_DIR) / "static" / "css"
CLASS = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
# Blocks whose braces hold no style rules of their own to compare.
OPAQUE = ("@keyframes", "@font-face", "@theme", "@property", "@-webkit-keyframes")

OVERRIDDEN_ON_PURPOSE = """
acct
acct__form--spaced
acct__hint
acct__nav
acct__out
auth-card
badge
btn
btn--ghost
buybox
buybox__cta
buybox__help
buybox__note
crumbs
deadline
faq
field
field__eye
grid
micro
nebula
nebula__item
nebula__mono
page-head__intro
page-head__title
pcard
pcard__body
pcard__cat
pcard__deal
pcard__foot
pcard__from
pcard__name
pcard__offer
pcard__pre
pcard__promotion
pcard__terms
pdp__title
plan--off
plan__promotion
plan__title
price
price__cur
price__num
price__was
prose
qa
rich
shelf__all
small
spec
stepper
stepper__btn
stepper__input
""".split()


def rule_classes(css: str, *, layered: bool) -> set[str]:
    """Class names in the selectors of `css`: those inside an `@layer` block when
    `layered`, else those outside every layer."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    found: set[str] = set()
    stack: list[str] = []  # "layer", "plain" or "opaque" per open brace
    start = 0
    for index, char in enumerate(css):
        if char in ";}":
            if char == "}" and stack:
                stack.pop()
            start = index + 1
        elif char == "{":
            prelude = css[start:index].strip()
            start = index + 1
            if prelude.startswith("@"):
                if prelude.startswith("@layer"):
                    stack.append("layer")
                elif prelude.startswith(OPAQUE):
                    stack.append("opaque")
                else:
                    stack.append("plain")
                continue
            in_layer = "layer" in stack
            if "opaque" not in stack and in_layer == layered:
                found.update(CLASS.findall(re.sub(r'"[^"]*"|\[[^\]]*\]', "", prelude)))
            stack.append("plain")
    return found


def test_a_class_is_never_defined_in_both_a_layer_and_the_unlayered_sheet():
    layered = rule_classes((CSS / "input.css").read_text(encoding="utf-8"), layered=True)
    unlayered = rule_classes((CSS / "storefront.css").read_text(encoding="utf-8"), layered=False)
    shared = sorted(layered & unlayered)
    added = sorted(set(shared) - set(OVERRIDDEN_ON_PURPOSE))
    removed = sorted(set(OVERRIDDEN_ON_PURPOSE) - set(shared))
    assert not added, (
        f"{added} now exist in an @layer of input.css and in storefront.css; the unlayered "
        "copy wins silently. Rename one, or add the name to OVERRIDDEN_ON_PURPOSE if the "
        "override is intended."
    )
    assert not removed, (
        f"{removed} no longer exist in both files; remove the name from "
        "OVERRIDDEN_ON_PURPOSE on purpose."
    )

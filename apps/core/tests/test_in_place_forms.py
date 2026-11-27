"""Which customer forms answer in place, and which still load a page on purpose.

A form that changes something on the page it sits on carries `data-in-place`, so
`static/js/in-place.js` swaps in the server's answer instead of reloading. Showing
delivery details opens a page of its own, which keeps credentials off the order page,
and signing out leaves the account, so neither is marked.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
FORM = re.compile(r"<form\b[^>]*>", re.S)

IN_PLACE = {
    "orders/_delivery.html": ["order-delivery-code"],
    "orders/_claim.html": ["order-claim"],
    "orders/_reviews.html": ["order-review"],
    "orders/detail.html": ["order-cancel", "order-input-submit", "order-support-replace"],
    "accounts/account.html": ["account-telegram", None],
}
LOADS_A_PAGE = {
    "orders/_delivery.html": ["order-delivery-reveal"],
    "accounts/_account_base.html": ["logout"],
}


def forms(template: str, route: str | None) -> list[str]:
    """The opening tags of the template's forms posting to the route; None is a form posting to its own page."""
    tags = FORM.findall((TEMPLATES / template).read_text(encoding="utf-8"))
    if route is None:
        return [tag for tag in tags if 'method="post"' in tag and "action=" not in tag]
    return [tag for tag in tags if f"'{route}'" in tag]


@pytest.mark.parametrize(
    "template, route", [(t, r) for t, routes in IN_PLACE.items() for r in routes]
)
def test_a_form_that_changes_its_own_page_answers_in_place(template, route):
    found = forms(template, route)
    assert found, f"{template} has no form for {route}"
    assert all("data-in-place" in tag for tag in found), found


@pytest.mark.parametrize(
    "template, route", [(t, r) for t, routes in LOADS_A_PAGE.items() for r in routes]
)
def test_reveal_and_sign_out_still_load_a_page(template, route):
    found = forms(template, route)
    assert found, f"{template} has no form for {route}"
    assert not any("data-in-place" in tag for tag in found), found


def test_every_page_loads_the_script():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "js/in-place.js" in base

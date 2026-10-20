"""What the shop promises on every page, and only what is true of every product.

A line in the header strip or the footer is read as true of whatever the
visitor is looking at. So a site-wide promise is defined here, beside the
condition it must meet for each product, and `test_promises.py` checks every
condition against every public product and checks that the shell prints
nothing else.

Missing on purpose, each until the owner has decided what the shop commits
to: the payment method, and the gateway itself at S5; refunds; warranty
terms. Delivery of login details through the account or the one-time link
waits for S9, which builds both.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.http import HttpRequest

from apps.catalog.models import Product
from apps.core.formatting import to_persian_digits

# The CHECK product_delivery_hours_1_to_72 (ADR-0009, amended 2026-10-18).
# The cap, not the slowest product: a number that moves whenever the operator
# edits one product is not a promise.
DELIVERY_CAP_HOURS = 72


@dataclass(frozen=True)
class Promise:
    text: str
    holds_for: Callable[[Product], bool]


PROMISES = (
    Promise(
        f"تحویل حداکثر {to_persian_digits(DELIVERY_CAP_HOURS)} ساعت پس از تأیید پرداخت",
        lambda product: product.delivery_hours <= DELIVERY_CAP_HOURS,
    ),
)


def context(request: HttpRequest) -> dict[str, object]:
    return {"promises": PROMISES, "delivery_cap_hours": DELIVERY_CAP_HOURS}

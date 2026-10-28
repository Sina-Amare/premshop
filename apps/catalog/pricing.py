"""One price rule, one function.

Every price the shop shows or charges — a catalog card, the product page, a
cart line, the checkout summary, the snapshot written onto an order — comes
through here. A promotion is a *pricing fact* with a window, not a discount row:
it needs no code, no redemption record and no order column (ADR-0021).

Duplicating this comparison anywhere else is the bug this module exists to
prevent. If a price is being computed and this function is not in the call
stack, that is the defect.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from apps.catalog.models import Plan


def effective_price(plan: Plan, at: datetime | None = None) -> Decimal:
    """The promotional price while its window is open, otherwise the list price.

    An absent bound is open-ended: no start means "already started", no end
    means "until further notice". The window is half-open — a promotion that
    ends at 10:00 is over at 10:00, not at 10:00:59.
    """
    if plan.promo_price is not None and plan.promo_window_open_at(at):
        return plan.promo_price
    return plan.sale_price


class MonthlyPrice(NamedTuple):
    price: Decimal  # a month, to the nearest 1,000 toman
    saving: int | None  # whole percent below the one-month plan, when at least 1


def monthly_price(
    plan: Plan, plans: Iterable[Plan], at: datetime | None = None
) -> MonthlyPrice | None:
    """What a plan of two months or more comes to a month, and how much less
    that is than the cheapest one-month plan among `plans` that can be bought.

    The saving is measured on the unrounded monthly figure and floored, so it
    never claims more than the two prices on the page show.
    """
    if plan.duration_days is None or plan.duration_days < 60:
        return None
    exact = Decimal(effective_price(plan, at)) / round(plan.duration_days / 30)
    price = exact.quantize(Decimal("1E3"), rounding=ROUND_HALF_UP)
    if not price:
        return None
    month = min(
        (
            effective_price(other, at)
            for other in plans
            if other.is_in_stock and 25 <= (other.duration_days or 0) <= 35
        ),
        default=None,
    )
    saving = math.floor((1 - exact / month) * 100) if month else 0
    return MonthlyPrice(price, saving if saving >= 1 else None)

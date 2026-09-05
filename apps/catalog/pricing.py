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

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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

"""The figures on a product page's پیش‌فاکتور, built on the server.

The page's script picks a total out of what is built here and computes none:
every available plan at every quantity it allows, now and after its discount
ends, with the amount in words.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from apps.catalog.models import Plan
from apps.catalog.pricing import effective_price
from apps.core.formatting import format_toman, toman_in_words

DAYS_PER_MONTH = Decimal("30.4375")  # 365.25 / 12, so 90 days is 3 months and 365 is 12


def _figure(amount: Decimal) -> dict[str, object]:
    if not amount:
        return {"amount": "رایگان", "words": "", "free": True}
    return {"amount": format_toman(amount, with_unit=False), "words": toman_in_words(amount)}


def _per_month(plan: Plan, price: Decimal) -> Decimal | None:
    """«ماهی …» under a plan of two months or more; a gift card has no duration."""
    if not plan.duration_days or not price:
        return None
    months = int((plan.duration_days / DAYS_PER_MONTH).quantize(Decimal(1), ROUND_HALF_UP))
    if months < 2:
        return None
    return (price / months).quantize(Decimal(1), ROUND_HALF_UP)


def one_discount_end(plans: Iterable[Plan]) -> Plan | None:
    """A plan whose discount ends when every live discount among `plans` does, or None.

    A discount with no end counts as an end of its own: one open-ended discount
    keeps a single date from being printed over all of them.
    """
    live = [plan for plan in plans if plan.promotion_is_active]
    ends = {plan.promo_ends_at for plan in live}
    return live[0] if len(ends) == 1 and None not in ends else None


def build(plans: list[Plan], wanted: str | None) -> dict[str, object]:
    """Context for `catalog/_buybox.html`.

    `wanted` is the `?plan=` a rate-sheet row links with. It is honoured only
    when it names an available plan of this product; otherwise the first
    available plan is chosen, never a sold-out one.
    """
    available = [plan for plan in plans if plan.is_available]
    chosen = next(
        (p for p in available if str(p.pk) == wanted), available[0] if available else None
    )
    rows, data = [], {}
    for plan in plans:
        now = effective_price(plan)
        after = effective_price(plan, at=plan.promo_ends_at) if plan.promo_ends_at else now
        rows.append(
            {
                "plan": plan,
                "per_month": _per_month(plan, now),
                "after": after,
                "after_per_month": _per_month(plan, after),
            }
        )
        if plan.is_available:
            data[str(plan.pk)] = {
                phase: {str(n): _figure(price * n) for n in range(1, plan.max_quantity + 1)}
                for phase, price in (("now", now), ("after", after))
            }
    ending = [plan for plan in available if plan.promo_seconds_left is not None]
    shared = one_discount_end(available)
    return {
        "invoice_rows": rows,
        "invoice_data": data,
        "chosen_plan": chosen,
        "invoice_total": data[str(chosen.pk)]["now"]["1"] if chosen else None,
        # One shared end prints once under the plans; ends that differ print on each plan.
        "shared_deadline": shared,
        "plan_clocks": bool(ending) and shared is None,
        "any_plan_takes_more_than_one": any(plan.max_quantity > 1 for plan in available),
    }

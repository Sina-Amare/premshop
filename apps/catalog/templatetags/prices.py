"""Template filters that need the catalog's price rule, which `fa` cannot import."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import template
from django.utils import timezone

from apps.catalog.models import Plan, Product
from apps.catalog.pricing import effective_price, monthly_price

register = template.Library()


@register.filter
def price_when_promo_ends(plan: Plan) -> Decimal:
    """What the plan costs from the moment its discount ends. The countdown
    swaps it in at zero, so the page never shows a price that has run out."""
    return effective_price(plan, at=plan.promo_ends_at)


@register.filter
def card_price_steps(product: Product) -> list[dict[str, object]]:
    """Server-computed starting prices after each upcoming offer expiry."""
    plans = product.available_plans()
    if not plans:
        return []
    now = timezone.now()
    ends = sorted(
        {
            p.promo_ends_at
            for p in plans
            if p.promo_price is not None and p.promo_ends_at and p.promo_ends_at > now
        }
    )
    steps = []
    for moment in [now, *ends]:
        plan = min(plans, key=lambda p: effective_price(p, at=moment))
        steps.append(
            {
                "plan": plan,
                "price": effective_price(plan, at=moment),
                "left": None if moment == now else max(0, int((moment - now).total_seconds())),
            }
        )
    return steps


@register.filter
def monthly_price_steps(plan: Plan, plans: list[Plan]) -> list[dict[str, Any]]:
    """The plan's monthly figure now and after each discount among `plans` ends,
    each with the seconds it lasts and the moment it ends, so the countdown can
    change it with the price beside it. Empty for a plan that has no monthly
    figure."""
    now = timezone.now()
    ends = sorted(
        {
            p.promo_ends_at
            for p in plans
            if p.promo_price is not None and p.promo_ends_at and p.promo_ends_at > now
        }
    )
    steps: list[dict[str, Any]] = []
    for moment in [now, *ends]:
        value = monthly_price(plan, plans, at=moment)
        if steps and steps[-1]["value"] == value:
            continue
        if steps:
            steps[-1]["left"] = max(0, int((moment - now).total_seconds()))
            steps[-1]["until"] = int(moment.timestamp())
        steps.append({"value": value, "left": None})
    return steps if any(step["value"] for step in steps) else []


@register.filter
def card_offer(offer: Plan, product: Product) -> dict[str, object]:
    """Whether a card's offer line prints its plan's price, and until when.

    Not while the «از» line names the same plan, or one figure is printed
    twice. `until` is the seconds until an expiry moves the «از» line onto this
    plan; the countdown then swaps the figures for the percentage.
    """
    steps = card_price_steps(product)
    return {
        "priced": not steps or steps[0]["plan"] != offer,
        "until": next((step["left"] for step in steps[1:] if step["plan"] == offer), None),
    }


@register.filter
def monthly_price_key(plan: Plan, plans: list[Plan]) -> str:
    """The line's figures and the moments they change at, but not the seconds
    left, which every poll counts down: the poll redraws the line only when one
    of them moved."""
    return " ".join(
        "{}/{}@{}".format(*(step["value"] or ("", "")), step.get("until", ""))
        for step in monthly_price_steps(plan, plans)
    )

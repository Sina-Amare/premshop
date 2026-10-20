"""Template filters that need the catalog's price rule, which `fa` cannot import."""

from __future__ import annotations

from decimal import Decimal

from django import template

from apps.catalog.models import Plan, Product
from apps.catalog.pricing import effective_price

register = template.Library()


@register.filter
def price_when_promo_ends(plan: Plan) -> Decimal:
    """What the plan costs from the moment its discount ends. The countdown
    swaps it in at zero, so the page never shows a price that has run out."""
    return effective_price(plan, at=plan.promo_ends_at)


@register.filter
def lead_when_promo_ends(product: Product) -> dict[str, object] | None:
    """The plan a card leads with once its cheapest plan's discount ends, and
    its price then. That plan's own regular price can be above another's: a
    card swapped in 390,000 while a 350,000 plan was on sale beside it."""
    lead = product.cheapest_plan
    if lead is None or lead.promo_ends_at is None:
        return None
    end = lead.promo_ends_at
    plan = min(product.available_plans(), key=lambda p: effective_price(p, at=end))
    return {"title": plan.title, "price": effective_price(plan, at=end)}

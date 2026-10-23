"""Template filters that need the catalog's price rule, which `fa` cannot import."""

from __future__ import annotations

from decimal import Decimal

from django import template
from django.utils import timezone

from apps.catalog.models import Plan, Product
from apps.catalog.pricing import effective_price

register = template.Library()


@register.filter
def price_when_promo_ends(plan: Plan) -> Decimal:
    """What the plan costs from the moment its discount ends. The countdown
    swaps it in at zero, so the page never shows a price that has run out."""
    return effective_price(plan, at=plan.promo_ends_at)


@register.filter
def card_price_steps(product: Product) -> list[dict[str, object]]:
    """Server-computed starting prices after each currently active offer ends."""
    plans = product.available_plans()
    if not plans:
        return []
    now = timezone.now()
    ends = sorted({p.promo_ends_at for p in plans if p.promotion_is_active and p.promo_ends_at})
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

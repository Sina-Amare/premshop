from typing import Any

from django import template

from apps.cart.services import purchase_options
from apps.catalog.models import Plan

register = template.Library()


@register.simple_tag(takes_context=True)
def cart_purchase(context: Any, plans: list[Plan], wanted: str = "") -> dict[str, Any]:
    request = None if context.get("snapshot") else context.get("request")
    options = purchase_options(request, plans)
    selectable = [option for option in options if option.selectable]
    # `wanted` is the raw ?plan= value: only the exact id of a selectable plan
    # here matches it, so a malformed, foreign or sold-out one keeps the first.
    chosen = next(
        (option for option in selectable if str(option.plan.pk) == wanted),
        selectable[0] if selectable else None,
    )
    return {
        "options": options,
        "selectable": selectable,
        "chosen": chosen,
        "maximum": max((option.plan.max_quantity for option in options), default=1),
        "initial_maximum": chosen.max_add if chosen else 1,
    }

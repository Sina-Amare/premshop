from typing import Any

from django import template

from apps.cart.services import purchase_options
from apps.catalog.models import Plan
from apps.core.formatting import format_countdown

register = template.Library()


@register.simple_tag(takes_context=True)
def cart_purchase(context: Any, plans: list[Plan]) -> dict[str, Any]:
    request = None if context.get("snapshot") else context.get("request")
    options = purchase_options(request, plans)
    selectable = [option for option in options if option.selectable]
    return {
        "options": options,
        "selectable": selectable,
        "maximum": max((option.plan.max_quantity for option in options), default=1),
        "initial_maximum": selectable[0].max_add if selectable else 1,
    }


@register.filter
def reservation_clock(seconds: int) -> str:
    return format_countdown(seconds)

"""What a rate sheet works out about the products it lists, which a template cannot."""

from __future__ import annotations

from collections.abc import Iterable

from django import template

from apps.catalog.invoice import one_discount_end
from apps.catalog.models import Plan, Product

register = template.Library()


@register.filter
def shared_deadline(
    products: Iterable[Product], more: Iterable[Product] | None = None
) -> Plan | None:
    """The one end every discount on the sheet shares, printed once in its head.

    None when the ends differ or one discount has none; then each timed row
    carries its own.
    """
    return one_discount_end(
        plan for product in [*products, *(more or [])] for plan in product.available_plans()
    )


@register.filter
def plan_column(products: Iterable[Product]) -> str:
    """A gift card's plans are amounts, not periods."""
    kinds = {product.delivery_type for product in products}
    return "مبلغ کارت" if kinds == {Product.DeliveryType.GIFT_CARD} else "مدت اشتراک"

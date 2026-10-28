"""The panel's first page: the catalogue at a glance and what needs the operator."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.http import HttpRequest
from django.utils import timezone

from apps.catalog.models import Plan, Product

# Long enough to notice on a day off, short enough not to list every running offer.
ENDING_SOON = timedelta(hours=48)


def dashboard_callback(request: HttpRequest, context: dict[str, Any]) -> dict[str, Any]:
    public = Product.objects.public()
    plans = list(Plan.objects.filter(product__in=public).select_related("product"))
    soon = timezone.now() + ENDING_SOON
    on_sale = [plan for plan in plans if plan.promotion_is_active]
    context["attention"] = {
        "published": public.count(),
        "on_sale": len(on_sale),
        "sold_out": [plan for plan in plans if not plan.is_available or plan.stock_quantity == 0],
        "ending": sorted(
            (plan for plan in on_sale if plan.promo_ends_at and plan.promo_ends_at <= soon),
            key=lambda plan: plan.promo_ends_at,
        ),
        "drafts": list(Product.objects.filter(status=Product.Status.DRAFT).order_by("name")),
    }
    return context

"""Departments used by the storefront header."""

from django.db.models import Count, Q
from django.http import HttpRequest

from apps.catalog.models import Category, Product


def navigation(request: HttpRequest) -> dict[str, object]:
    try:
        categories = list(
            Category.objects.annotate(
                product_count=Count("products", filter=Q(products__status=Product.Status.ACTIVE))
            )
            .filter(product_count__gt=0)
            .order_by("sort_order", "name")
        )
    except Exception:  # noqa: BLE001 — navigation must not break an error page.
        categories = []
    return {"nav_categories": categories}

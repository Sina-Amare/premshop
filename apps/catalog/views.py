"""Thin controllers for the public catalog: fetch, filter through `public()`, render.

Every visitor-facing query goes through `Product.objects.public()`. A draft
product must never leak — not through a category listing, not through a search,
not through a guessed URL — and one filter used everywhere is how that stays
true when the fourth and fifth views are added.
"""

from __future__ import annotations

from django.db.models import Count, Prefetch, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.catalog.models import Category, Plan, Product, ProductQuerySet
from apps.core import ratelimit

SEARCH_RESULT_CAP = 50


def _for_cards() -> ProductQuerySet:
    """Products with everything a card needs, in two queries however many rows.

    The plans prefetch is what keeps the grid at a fixed query count: the card
    reads `cheapest_plan`, which iterates the prefetched list instead of asking
    the database per product.
    """
    return (
        Product.objects.public()
        .select_related("category")
        .prefetch_related(Prefetch("plans", queryset=Plan.objects.order_by("sort_order", "id")))
    )


def home(request: HttpRequest) -> HttpResponse:
    categories = (
        Category.objects.annotate(
            product_count=Count("products", filter=Q(products__status=Product.Status.ACTIVE))
        )
        .filter(product_count__gt=0)
        .order_by("sort_order", "name")
    )
    # "Featured" is the newest active products until the operator asks for a
    # flag; with a catalog this size the newest are the ones worth showing.
    featured = _for_cards().order_by("-created_at")[:8]
    return render(request, "catalog/home.html", {"categories": categories, "featured": featured})


def category(request: HttpRequest, slug: str) -> HttpResponse:
    cat = get_object_or_404(Category, slug=slug)
    products = _for_cards().filter(category=cat)
    delivery = request.GET.get("type", "")
    if delivery in Product.DeliveryType.values:
        products = products.filter(delivery_type=delivery)
    else:
        delivery = ""
    return render(
        request,
        "catalog/category.html",
        {
            "category": cat,
            "products": products,
            "delivery": delivery,
            "delivery_types": Product.DeliveryType.choices,
        },
    )


def product(request: HttpRequest, slug: str) -> HttpResponse:
    obj = get_object_or_404(_for_cards().prefetch_related("specs"), slug=slug)
    plans = list(obj.plans.all())  # prefetched: available and not, so the disabled state can render
    return render(
        request,
        "catalog/product.html",
        {
            "product": obj,
            "plans": plans,
            "available_plans": [p for p in plans if p.is_available],
            "specs": list(obj.specs.all()),
        },
    )


def search(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:100]
    limited = bool(query) and not ratelimit.hit(
        ratelimit.SEARCH_PER_IP, ratelimit.client_ip(request)
    )
    results = [] if (limited or not query) else list(_for_cards().search(query)[:SEARCH_RESULT_CAP])
    return render(
        request,
        "catalog/search.html",
        {"query": query, "results": results, "limited": limited},
        status=429 if limited else 200,
    )

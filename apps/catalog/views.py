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
HOME_PRODUCT_CAP = 24


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
    # Each department carries its own marks, because the home page shows what
    # is inside a department rather than describing it. One extra query for
    # all of them, not one per row.
    categories = (
        Category.objects.annotate(
            product_count=Count("products", filter=Q(products__status=Product.Status.ACTIVE))
        )
        .filter(product_count__gt=0)
        .prefetch_related(
            Prefetch(
                "products",
                queryset=Product.objects.public().order_by("name").only("name", "slug", "image"),
                to_attr="public_products",
            )
        )
        .order_by("sort_order", "name")
    )
    # Every active product, newest first. The cap was eight while the home
    # page also carried a separate shelf of marks; now that the shelf is the
    # hero, a count that says ۱۰ over a grid of eight is just a page
    # contradicting itself. The cap comes back the moment the catalogue is
    # bigger than one screen of scrolling, with paging behind it.
    featured = _for_cards().order_by("-created_at")[:HOME_PRODUCT_CAP]
    # The shelf is the complete index — every service the shop stocks, as a
    # mark you can tap. It answers a different question from the cards below
    # ("do you have it" rather than "what does it cost"), so it carries no
    # plans and needs none of the prefetching the cards do: one flat query.
    shelf = Product.objects.public().order_by("name").only("name", "slug", "image")
    # Real discounts only. `promotion_is_active` consults the window as well as
    # the price, so a promotion whose end date has passed drops out of this row
    # on its own — a shop that advertises an expired discount has done worse
    # than not advertising one.
    discounted = [p for p in featured if p.cheapest_plan and p.cheapest_plan.promotion_is_active]
    return render(
        request,
        "catalog/home.html",
        {
            "categories": categories,
            "featured": featured,
            "shelf": shelf,
            "discounted": discounted,
        },
    )


def category(request: HttpRequest, slug: str) -> HttpResponse:
    cat = get_object_or_404(Category, slug=slug)
    products = list(_for_cards().filter(category=cat))
    return render(
        request,
        "catalog/category.html",
        {"category": cat, "current_category": cat.slug, "products": products},
    )


def product(request: HttpRequest, slug: str) -> HttpResponse:
    obj = get_object_or_404(_for_cards().prefetch_related("specs"), slug=slug)
    plans = list(obj.plans.all())  # prefetched: available and not, so the disabled state can render
    available = [p for p in plans if p.is_available]
    # The quantity control is rendered server-side only when some plan can
    # actually take more than one, so a visitor with JavaScript off never sees
    # a stepper that cannot do anything.
    ceilings = [p.max_quantity for p in available] or [1]
    return render(
        request,
        "catalog/product.html",
        {
            "product": obj,
            "plans": plans,
            "available_plans": available,
            "any_plan_takes_more_than_one": max(ceilings) > 1,
            "max_quantity": max(ceilings),
            "specs": list(obj.specs.all()),
            # The facts the operator marked important enough to print beside
            # the title. Nothing is key by default, so a product the operator
            # has not thought about shows exactly what it showed before.
            "key_specs": [spec for spec in obj.specs.all() if spec.is_key],
        },
    )


def faq(request: HttpRequest) -> HttpResponse:
    """Static answers, in a template rather than a model.

    Six questions that change when the shop's promises change — which is a
    code change, in the same commit as the promise. A FAQ table in the
    database would let the answers drift away from the constraints that make
    them true (ADR-0014: no content model before there is content to model).
    """
    return render(request, "catalog/faq.html", {"current_category": "faq"})


def search(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:100]
    limited = bool(query) and not ratelimit.hit(
        ratelimit.SEARCH_PER_IP, ratelimit.client_ip(request)
    )
    results = [] if (limited or not query) else list(_for_cards().search(query)[:SEARCH_RESULT_CAP])
    # A miss shows the whole shelf. Every credible Iranian site answers an
    # empty search with one concrete thing to do next, and at ten products the
    # most useful thing by a distance is the catalogue itself: someone who
    # typed «چت جپت» sees ChatGPT on the page without touching the keyboard
    # again. Telling them to check their spelling is advice, not an answer.
    everything = [] if (results or limited) else list(_for_cards().order_by("name"))
    return render(
        request,
        "catalog/search.html",
        {"query": query, "results": results, "limited": limited, "everything": everything},
        status=429 if limited else 200,
    )

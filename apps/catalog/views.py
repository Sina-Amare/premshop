"""Thin controllers for the public catalog: fetch, filter through `public()`, render.

Every visitor-facing query goes through `Product.objects.public()`. A draft
product must never leak — not through a category listing, not through a search,
not through a guessed URL — and one filter used everywhere is how that stays
true when the fourth and fifth views are added. The one exception is the
operator's preview of a draft on its own address, in `product`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from django.db.models import Count, Prefetch, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_cache_control

from apps.catalog.models import Category, Plan, Product, ProductQuerySet
from apps.catalog.search import without_filler
from apps.core import ratelimit

SEARCH_RESULT_CAP = 50
HOME_PRODUCT_CAP = 24


def _for_cards(products: ProductQuerySet | None = None) -> ProductQuerySet:
    """Products with everything a card needs, in two queries however many rows.

    The plans prefetch is what keeps the grid at a fixed query count: the card
    reads `cheapest_plan`, which iterates the prefetched list instead of asking
    the database per product.
    """
    return (
        (Product.objects.public() if products is None else products)
        .select_related("category")
        .prefetch_related(Prefetch("plans", queryset=Plan.objects.order_by("sort_order", "id")))
    )


def _prices_checked_since(products: Iterable[Product]) -> datetime | None:
    """When the stalest price on the page was last confirmed, or None if one never was.

    The oldest and not the newest: one plan confirmed this morning must not make
    a list of week-old prices look fresh.
    """
    stamps = [plan.price_confirmed_at for product in products for plan in product.available_plans()]
    if not stamps or None in stamps:
        return None
    return min(stamp for stamp in stamps if stamp is not None)


def home(request: HttpRequest) -> HttpResponse:
    # Headings for the product grid, in the operator's order. The count is
    # over every active product in the department, which can exceed what the
    # cap below lets onto the page; that is what the link out is for.
    categories = (
        Category.objects.annotate(
            product_count=Count("products", filter=Q(products__status=Product.Status.ACTIVE))
        )
        .filter(product_count__gt=0)
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
    discounted = [p for p in featured if p.discounted_plans]
    # Grouped by department rather than one flat grid, which also retired the
    # separate «دسته‌بندی‌ها» block above it: that block and this one listed
    # the same four departments in the same order, four hundred pixels apart.
    # Costs no query — both sides are already in memory, and a product belongs
    # to exactly one department.
    by_category = [(cat, [p for p in featured if p.category_id == cat.id]) for cat in categories]
    by_category = [(cat, items) for cat, items in by_category if items]
    return render(
        request,
        "catalog/home.html",
        {
            "featured": featured,
            "shelf": shelf,
            "discounted": discounted,
            "by_category": by_category,
            "prices_checked_since": _prices_checked_since(featured),
        },
    )


def category(request: HttpRequest, slug: str) -> HttpResponse:
    cat = get_object_or_404(Category, slug=slug)
    products = list(_for_cards().filter(category=cat))
    return render(
        request,
        "catalog/category.html",
        {
            "category": cat,
            "current_category": cat.slug,
            "products": products,
            "prices_checked_since": _prices_checked_since(products),
        },
    )


def product(request: HttpRequest, slug: str) -> HttpResponse:
    # The operator previews a draft at the address it will be published on. For
    # anyone else a draft does not exist, and «ناموجود» stays hidden for everyone.
    may_preview = request.user.is_staff and request.user.has_perm("catalog.view_product")
    shown = (
        Product.objects.filter(status__in=[Product.Status.ACTIVE, Product.Status.DRAFT])
        if may_preview
        else Product.objects.public()
    )
    obj = get_object_or_404(
        _for_cards(shown).prefetch_related("specs", "sections", "questions"), slug=slug
    )
    plans = list(obj.plans.all())  # prefetched: available and not, so the disabled state can render
    available = [p for p in plans if p.is_available]
    # The quantity control is rendered server-side only when some plan can
    # actually take more than one, so a visitor with JavaScript off never sees
    # a stepper that cannot do anything.
    ceilings = [p.max_quantity for p in available] or [1]
    related = _related_to(obj)
    sections = sorted(obj.sections.all(), key=lambda s: (s.sort_order, s.pk))
    before_buying = next((s for s in sections if s.kind == "before_buying"), None)
    response = render(
        request,
        "catalog/product.html",
        {
            "product": obj,
            "preview": not obj.is_public,
            "plans": plans,
            "available_plans": available,
            "any_plan_takes_more_than_one": max(ceilings) > 1,
            "max_quantity": max(ceilings),
            "specs": list(obj.specs.all()),
            "related": related,
            # The facts the operator marked important enough to print beside
            # the title. Nothing is key by default, so a product the operator
            # has not thought about shows exactly what it showed before.
            "key_specs": [spec for spec in obj.specs.all() if spec.is_key],
            "sections": sections,
            # At most one, by a constraint. Its facts come from their own fields,
            # so it is printed without a row too, but only when it says more than
            # the warranty, which the badge and the answers already give.
            "before_buying": before_buying,
            "before_buying_shown": bool(
                (before_buying and (before_buying.body or before_buying.image))
                or obj.refund_note
                or obj.usage_notes_shown
            ),
            "after_payment": next((s for s in sections if s.kind == "after_payment"), None),
            "questions": sorted(obj.questions.all(), key=lambda q: (q.sort_order, q.pk)),
            "price_checked_since": _prices_checked_since([obj]),
            "usage_notes": obj.usage_notes_shown,
        },
    )
    if not obj.is_public:
        # The preview is on the public address, so nothing between here and the
        # browser may keep it for whoever asks for that address next.
        patch_cache_control(response, private=True, no_store=True)
    return response


RELATED_COUNT = 4


def _related_to(product: Product) -> list[Product]:
    """What to show someone at the bottom of a product page.

    Ranked, not random: same department first, then anything with a live
    discount, then the newest. There is no "customers also bought" here and
    there will not be until there are customers — no order table exists yet,
    so any claim about what is popular would be invented. These three signals
    are all true today, which is the whole bar.

    Two queries however many products. The candidates used to be cut to the
    first four by name before this ranking ever saw them, so a discount in
    another department lost its place to whatever sorted first.
    shortcut: ranks every public product in Python, right for a few dozen; at a
    few hundred, move the department and discount tests into the query.
    """

    def rank(other: Product) -> tuple[bool, bool, float]:
        plan = other.cheapest_plan
        return (
            other.category_id != product.category_id,
            not (plan and plan.promotion_is_active),
            -other.created_at.timestamp(),
        )

    return sorted(_for_cards().exclude(pk=product.pk), key=rank)[:RELATED_COUNT]


def faq(request: HttpRequest) -> HttpResponse:
    """Static answers, in a template rather than a model.

    Questions that change when the shop's promises change — which is a
    code change, in the same commit as the promise. A FAQ table in the
    database would let the answers drift away from the constraints that make
    them true (ADR-0014: no content model before there is content to model).
    """
    return render(request, "catalog/faq.html", {"current_category": "faq"})


def search(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:100]
    # Submitting an empty box used to render a heading over nothing, after
    # fetching the whole catalogue and discarding it. Home already *is* the
    # every-product page, and grouped by department rather than flat, so the
    # honest answer to "show me everything" is to go there.
    if not query:
        return redirect("home")
    limited = not ratelimit.hit(ratelimit.SEARCH_PER_IP, ratelimit.client_ip(request))
    terms = without_filler(query)
    results = [] if limited else list(_for_cards().search(terms)[:SEARCH_RESULT_CAP])
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

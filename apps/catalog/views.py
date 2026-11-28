"""Catalog listings use for-sale products; detail also admits eligible unavailable pages."""

from __future__ import annotations

import random
from decimal import ROUND_HALF_UP, Decimal
from itertools import zip_longest

from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control

from apps.catalog.models import Category, Plan, Product, ProductQuerySet
from apps.catalog.pricing import effective_price
from apps.catalog.search import without_filler
from apps.catalog.seo import breadcrumb_schema, product_breadcrumb, product_metadata, website_schema
from apps.core import ratelimit
from apps.orders.models import Review

SEARCH_RESULT_CAP = 50
SUGGESTION_CAP = 5
SUGGESTION_MIN_LENGTH = 2
HOME_PRODUCT_CAP = 24
ORBIT_SIZE = 12  # eight on the outer ring, four on the inner
CATEGORY_SORTS = ("new", "cheap", "expensive")


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
    # The shelf is every service the shop stocks, departments taking turns, in
    # one flat query; the hero's orbit shows its first twelve.
    index = list(Product.objects.public().order_by("created_at", "id").only("category_id"))
    shelf = [
        item
        for group in zip_longest(
            *[[item for item in index if item.category_id == cat.pk] for cat in categories]
        )
        for item in group
        if item is not None
    ][:ORBIT_SIZE]
    # The orbit's centre names each one's price and delivery, as its card does. A
    # service withdrawn between the two queries is skipped.
    loaded = _for_cards().in_bulk([item.pk for item in shelf])
    orbit = [loaded[item.pk] for item in shelf if item.pk in loaded]
    # Real discounts only. `promotion_is_active` consults the window as well as
    # the price, so a promotion whose end date has passed drops out of this row
    # on its own — a shop that advertises an expired discount has done worse
    # than not advertising one.
    discounted = [p for p in featured if p.discounted_plans]
    # The hero's "cheaper" is this number, not an adjective nothing backs.
    offer_peak = max(
        (plan.discount_percent for p in discounted for plan in p.discounted_plans), default=0
    )
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
            "orbit": orbit,
            "discounted": discounted,
            "offer_peak": offer_peak,
            "by_category": by_category,
            "quick_plan_products": featured,
            "structured_data": website_schema(),
        },
    )


def _by_price(products: list[Product], sort: str) -> list[Product]:
    """The grid ordered by the price its card leads with: the lowest effective
    price among the plans that can be bought, read at one moment for the whole
    page. A product with nothing to buy has no price and goes last either way.
    Python's sort is stable, so equal prices keep the order they came in."""
    now = timezone.now()

    def key(product: Product) -> tuple[int, Decimal]:
        prices = [effective_price(plan, now) for plan in product.available_plans()]
        if not prices:
            return (1, Decimal(0))
        return (0, min(prices) if sort == "cheap" else -min(prices))

    return sorted(products, key=key)


def category(request: HttpRequest, slug: str) -> HttpResponse:
    cat = get_object_or_404(Category, slug=slug)
    # An unknown value is the default, not an error: the address is typed and
    # shared by hand, and a stale link must still show the shelf.
    sort = request.GET.get("sort", "")
    sort = sort if sort in CATEGORY_SORTS else "new"
    products = list(_for_cards().filter(category=cat).order_by("-created_at", "-pk"))
    if sort != "new":
        products = _by_price(products, sort)
    return render(
        request,
        "catalog/category.html",
        {
            "category": cat,
            "current_category": cat.slug,
            "sort": sort,
            "products": products,
            "quick_plan_products": products,
            "structured_data": breadcrumb_schema(
                [(cat.name, reverse("category", args=[cat.slug]))]
            ),
        },
    )


def product(request: HttpRequest, slug: str) -> HttpResponse:
    may_preview = request.user.is_staff and request.user.has_perm("catalog.view_product")
    shown = _for_cards(Product.objects.filter(slug=slug)).prefetch_related(
        "specs", "sections", "questions"
    )
    obj = next(shown.public_pages(), None)
    public = obj is not None
    if obj is None and may_preview:
        obj = shown.filter(status=Product.Status.DRAFT).first()
    if obj is None:
        raise Http404
    rows = (
        Review.objects.filter(order_item__plan__product=obj, status=Review.Status.PUBLISHED)
        .order_by("-created_at", "-pk")
        .values_list("rating", "text", "created_at", "order_item__product_snapshot__plan_title")
    )
    reviews = [
        {"rating": rating, "text": text, "created_at": at, "plan": plan}
        for rating, text, at, plan in (rows if public else [])
    ]
    average, clip, bars = "", (0.0, 0.0), []
    if reviews:
        mean = Decimal(sum(row["rating"] for row in reviews)) / len(reviews)
        # Five 24-wide stars lit from the right, the reading side.
        clip = (round(float(120 - mean * 24), 1), round(float(mean * 24), 1))
        average = format(mean.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")
        average = average.removesuffix(".0").replace(".", "٫")
        bars = [
            {"stars": stars, "count": sum(row["rating"] == stars for row in reviews)}
            for stars in range(5, 0, -1)
        ]
    response = render(
        request,
        "catalog/product.html",
        {
            **product_context(obj, related=_related_to(obj)),
            "show_reviews": public,
            "product_reviews": reviews,
            "review_average": average,
            "review_clip": clip,
            "review_bars": bars,
            "structured_data": (
                product_breadcrumb(obj) if obj.status != Product.Status.DRAFT else None
            ),
            # A quick-plans row links here with its plan; the buy box decides
            # whether that plan can be chosen.
            "wanted_plan": request.GET.get("plan", ""),
        },
    )
    if obj.status == Product.Status.DRAFT:
        # The preview is on the public address, so nothing between here and the
        # browser may keep it for whoever asks for that address next.
        patch_cache_control(response, private=True, no_store=True)
    return response


def product_context(obj: Product, *, related: list[Product] | None = None) -> dict[str, object]:
    plans = list(obj.plans.all())  # prefetched: available and not, so the disabled state can render
    available = [p for p in plans if p.is_in_stock]
    # The quantity control is rendered server-side only when some plan can
    # actually take more than one, so a visitor with JavaScript off never sees
    # a stepper that cannot do anything.
    ceilings = [p.max_quantity for p in available] or [1]
    sections = sorted(obj.sections.all(), key=lambda s: (s.sort_order, s.pk))
    before_buying = next((s for s in sections if s.kind == "before_buying"), None)
    return {
        "product": obj,
        "preview": obj.status == Product.Status.DRAFT,
        "plans": plans,
        "available_plans": available,
        "any_plan_takes_more_than_one": max(ceilings) > 1,
        "max_quantity": max(ceilings),
        "specs": list(obj.specs.all()),
        "related": related or [],
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
        "quick_plan_products": [obj, *(related or [])],
        "usage_notes": obj.usage_notes_shown,
        **product_metadata(obj),
    }


RELATED_COUNT = 4


def _related_to(product: Product) -> list[Product]:
    """What to show someone at the bottom of a product page: a shuffle of the rest of the shop.

    Other things a visitor might like, from any department and in a new order on
    each visit, so a second look shows something new. The heading claims nothing
    about the visitor: there are no orders yet to learn tastes from.

    Two queries however many products.
    shortcut: samples every public product in Python, right for a few dozen; at a
    few hundred, sample ids in the query instead.
    """
    others = list(_for_cards().exclude(pk=product.pk))
    # Display order only; nothing secret rides on it.
    return random.sample(others, min(RELATED_COUNT, len(others)))  # noqa: S311  # nosec B311


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
        {
            "query": query,
            "results": results,
            "limited": limited,
            "everything": everything,
            # Telegram pre-enters ?text= on a username link into the chat's
            # input bar (core.telegram.org/api/links), so a miss reaches
            # support already saying what was wanted.
            "telegram_draft": f"سلام، دنبال \u2068{query}\u2069 هستم.",
            "quick_plan_products": results or everything,
        },
        status=429 if limited else 200,
    )


def search_suggest(request: HttpRequest) -> JsonResponse:
    """The header's list while the buyer types: the first rows of the same search.

    Same matching and the same `public()` filter as the results page. Typing
    asks far more often than a person submits, so the list spends a budget of
    its own; it only reads the page's, and stays quiet once that is spent, so it
    is never a way round it. A query too short to mean anything once its filler
    words are dropped is answered before either budget or the database.
    """
    terms = without_filler(request.GET.get("q", "").strip()[:100])
    if len(terms) < SUGGESTION_MIN_LENGTH:
        return JsonResponse({"results": []})
    ip = ratelimit.client_ip(request)
    if not ratelimit.remaining(ratelimit.SEARCH_PER_IP, ip) or not ratelimit.hit(
        ratelimit.SEARCH_SUGGEST_PER_IP, ip
    ):
        return JsonResponse({"results": []}, status=429)
    found = _for_cards().search(terms)[:SUGGESTION_CAP]
    return JsonResponse({"results": [_suggestion(product) for product in found]})


def _suggestion(product: Product) -> dict[str, str]:
    # Rendered here rather than built in the script, so the row is escaped by
    # the template and the mark is the card's own include.
    plans = product.available_plans()
    row = {
        "product": product,
        "price": min(effective_price(plan) for plan in plans) if plans else None,
        "several": len(plans) > 1,
    }
    return {
        "url": product.get_absolute_url(),
        "html": render_to_string("catalog/_suggestion.html", row),
    }

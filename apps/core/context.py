"""What every page needs from the shell, fetched once.

Two things live here rather than in each view. The shelves row is in the header
of every page, so making each view remember to supply it is a bug waiting for
the fourth view. The answering hours are a promise the shop makes in three
places — the header strip, the footer and the product FAQ — and a promise
stated three times from three literals is a promise that will eventually
disagree with itself; here it has one definition.
"""

from __future__ import annotations

from django.db.models import Count, Q
from django.http import HttpRequest

# The operator answers between these hours, every day. Persian digits, because
# this is display text and never parsed back.
SUPPORT_HOURS = "۱۰ تا ۲۲"


def shell(request: HttpRequest) -> dict[str, object]:
    """One extra query per page, and only for pages that render the header.

    Deliberately not cached: a category appearing in the nav the moment the
    operator publishes its first product is worth more than four milliseconds,
    and a stale shelf row pointing at an empty department is the kind of small
    wrongness this shop cannot afford.
    """
    from apps.catalog.models import Category, Product

    try:
        categories = list(
            Category.objects.annotate(
                product_count=Count("products", filter=Q(products__status=Product.Status.ACTIVE))
            )
            .filter(product_count__gt=0)
            .order_by("sort_order", "name")
        )
    except Exception:  # noqa: BLE001
        # Evaluated here, inside the guard, on purpose: a lazy queryset would
        # raise during template rendering instead, and the one template that
        # matters most is 500.html. A shelves row is decoration; it must never
        # be the reason the error page itself fails to render, or a database
        # outage turns into a blank response with no explanation in it.
        categories = []
    return {"nav_categories": categories, "SUPPORT_HOURS": SUPPORT_HOURS}

from django.db import DatabaseError
from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from apps.cart.services import cart_count


def context(request: HttpRequest) -> dict[str, object]:
    def count() -> int:
        if not hasattr(request, "user") or not hasattr(request, "session"):
            return 0
        try:
            return cart_count(request)
        except DatabaseError:
            # The error page's header must also render during a database outage.
            return 0

    return {"cart_count": SimpleLazyObject(count)}

from typing import cast

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.accounts.models import User
from apps.orders.models import Order


@never_cache
@login_required
@require_GET
def detail(request: HttpRequest, order_number: int) -> HttpResponse:
    order = get_object_or_404(
        Order.objects.filter(user=cast(User, request.user)), order_number=order_number
    )
    return render(
        request,
        "orders/detail.html",
        {
            "order": order,
            "items": order.items.defer("customer_input").order_by("pk"),
            "section": "orders",
        },
    )

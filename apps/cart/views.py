from collections.abc import Callable
from math import ceil

from django import forms
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.cart import services
from apps.cart.models import CartItem
from apps.catalog.models import Plan, Product
from apps.core.formatting import format_countdown, format_toman, to_persian_digits


class LineForm(forms.Form):
    plan = forms.IntegerField(min_value=1)
    quantity = forms.IntegerField(min_value=0, required=False)
    step = forms.IntegerField(min_value=-1, max_value=1, required=False)


def _page(request: HttpRequest, *, error: str = "", status: int = 200) -> HttpResponse:
    return render(
        request,
        "cart/detail.html",
        {"cart": services.cart_summary(services.get_cart(request)), "error": error},
        status=status,
    )


@never_cache
@require_GET
def detail(request: HttpRequest) -> HttpResponse:
    return _page(request)


def _change(request: HttpRequest, operation: Callable[..., object]) -> HttpResponse:
    wants_json = request.headers.get("Accept") == "application/json"

    def error(message: str) -> HttpResponse:
        if wants_json:
            return JsonResponse({"message": message}, status=400)
        return _page(request, error=message, status=400)

    form = LineForm(request.POST)
    if not form.is_valid():
        return error("تعداد را با عدد وارد کنید و دوباره امتحان کنید.")
    kwargs = {"plan_id": form.cleaned_data["plan"]}
    if operation is not services.remove_from_cart:
        quantity = form.cleaned_data["quantity"]
        if quantity is None and operation is services.add_to_cart:
            quantity = 1
        if quantity is None:
            return error("تعداد را وارد کنید.")
        if operation is services.set_quantity:
            quantity += form.cleaned_data["step"] or 0
        kwargs["quantity"] = quantity
    new_guest = not request.user.is_authenticated and request.session.session_key is None
    try:
        result = operation(request, **kwargs)
    except ValidationError as exc:
        if new_guest:
            request.session.flush()
        return error(" ".join(exc.messages))
    if wants_json:
        plans = list(
            Plan.objects.filter(
                product__plans__pk=kwargs["plan_id"], product__in=Product.objects.public()
            ).select_related("product")
        )
        data = _state(request, plans)
        plan = next((plan for plan in plans if plan.pk == kwargs["plan_id"]), None)
        name = f"{plan.product.name}، {plan.title}" if plan else "محصول"
        removed = operation is services.remove_from_cart or kwargs.get("quantity") == 0
        if removed:
            data["message"] = f"{name} از سبد خرید حذف شد."
        elif operation is services.set_quantity:
            data["message"] = f"تعداد {name} در سبد خرید به‌روز شد."
        else:
            data["message"] = f"{name} به سبد خرید اضافه شد."
        if (
            isinstance(result, CartItem)
            and result.reserved_until
            and result.reserved_until > timezone.now()
        ):
            left = ceil((result.reserved_until - timezone.now()).total_seconds())
            data["hold_message"] = f"مهلت رزرو در سبد خرید: {format_countdown(left)}."
        return JsonResponse(data)
    return redirect("cart")


def _state(request: HttpRequest, plans: list[Plan]) -> dict[str, object]:
    count = services.cart_count(request)
    return {
        "count": count,
        "count_text": to_persian_digits(count),
        "count_label": f"سبد خرید، {to_persian_digits(count)} عدد",
        "options": {
            str(option.plan.pk): {
                "max_add": option.max_add,
                "in_cart": option.in_cart,
                "maximum": option.maximum,
                "available": option.plan.is_available and option.maximum > 0,
                "selectable": option.selectable,
                "reserved_until": (
                    option.reserved_until.isoformat() if option.reserved_until else None
                ),
                "expired": option.plan.stock_quantity is not None
                and option.reserved_until is not None
                and option.reserved_until <= timezone.now(),
                "reservation_required": option.plan.stock_quantity is not None
                and option.in_cart > 0
                and option.reserved_until is None,
                "price_html": render_to_string(
                    "catalog/_price.html", {"plan": option.plan}
                ).strip(),
                "price_text": format_toman(option.plan.effective_price, with_unit=False),
                "price_key": (
                    format_toman(option.plan.effective_price, with_unit=False)
                    + "|"
                    + (
                        str(int(option.plan.promo_ends_at.timestamp()))
                        if option.plan.promo_ends_at
                        else ""
                    )
                ),
            }
            for option in services.purchase_options(request, plans)
        },
    }


@never_cache
@require_GET
def availability(request: HttpRequest) -> HttpResponse:
    values = request.GET.getlist("plan")
    if len(values) > 50 or any(not value.isdecimal() or len(value) > 10 for value in values):
        return JsonResponse({"message": "صفحه را دوباره باز کنید."}, status=400)
    plans = list(Plan.objects.filter(pk__in=values, product__in=Product.objects.public()))
    return JsonResponse(_state(request, plans))


@never_cache
@require_POST
def add(request: HttpRequest) -> HttpResponse:
    return _change(request, services.add_to_cart)


@never_cache
@require_POST
def quantity(request: HttpRequest) -> HttpResponse:
    return _change(request, services.set_quantity)


@never_cache
@require_POST
def remove(request: HttpRequest) -> HttpResponse:
    return _change(request, services.remove_from_cart)

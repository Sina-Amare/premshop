from collections.abc import Callable

from django import forms
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.cart import services


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
    form = LineForm(request.POST)
    if not form.is_valid():
        return _page(request, error="اطلاعات سبد خرید درست نیست.", status=400)
    kwargs = {"plan_id": form.cleaned_data["plan"]}
    if operation is not services.remove_from_cart:
        quantity = form.cleaned_data["quantity"]
        if quantity is None and operation is services.add_to_cart:
            quantity = 1
        if quantity is None:
            return _page(request, error="تعداد را وارد کنید.", status=400)
        if operation is services.set_quantity:
            quantity += form.cleaned_data["step"] or 0
        kwargs["quantity"] = quantity
    try:
        operation(request, **kwargs)
    except ValidationError as exc:
        return _page(request, error=" ".join(exc.messages), status=400)
    return redirect("cart")


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

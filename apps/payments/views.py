import uuid

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.forms import CodeForm, EmailForm
from apps.cart.services import cart_summary, get_cart
from apps.orders.services import UnpaidOrderExists
from apps.payments import services
from apps.payments.forms import CheckoutForm


def _page(
    request: HttpRequest,
    *,
    discount_code: str = "",
    error: str = "",
    status: int = 200,
) -> HttpResponse:
    quote = None
    existing = None
    if request.user.is_authenticated:
        try:
            quote = services.quote_checkout(user=request.user, discount_code=discount_code)
        except UnpaidOrderExists as exc:
            existing = exc.order
        except ValidationError as exc:
            error = error or " ".join(exc.messages)
            if request.method == "POST" and status == 200:
                status = 400
    initial = {
        "checkout_id": uuid.uuid4(),
        "quote": quote.token if quote else "",
        "discount_code": discount_code,
        "phone": request.POST.get("phone", "")[:32],
        "activation_consent": False,
    }
    if quote:
        for line in quote.purchase.lines:
            key = f"customer_input_{line.plan.pk}"
            initial[key] = request.POST.get(key, "")[:2000]
    return render(
        request,
        "payments/checkout.html",
        {
            "quote": quote,
            "existing": existing,
            "error": error,
            "form": CheckoutForm(initial=initial, purchase=quote.purchase if quote else None),
            "cart": cart_summary(get_cart(request)),
            "email_form": EmailForm(),
            "code_form": CodeForm(),
        },
        status=status,
    )


@never_cache
@require_http_methods(["GET", "POST"])
def checkout(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return _page(request)
    if not request.user.is_authenticated:
        return redirect_to_login(reverse("checkout"))
    user = request.user
    code = request.POST.get("discount_code", "")[:32]
    action = request.POST.get("action")
    if action == "review":
        try:
            services.prepare_checkout(user=user)
        except ValidationError as exc:
            return _page(request, discount_code=code, error=" ".join(exc.messages), status=400)
        return _page(request, discount_code=code)
    form = CheckoutForm(request.POST)
    if action != "checkout" or not form.is_valid():
        error = " ".join(str(value) for values in form.errors.values() for value in values)
        return _page(
            request, discount_code=code, error=error or "درخواست ثبت سفارش معتبر نیست.", status=400
        )
    try:
        order = services.checkout(
            user=user,
            quote=form.cleaned_data["quote"],
            checkout_id=form.cleaned_data["checkout_id"],
            phone=form.cleaned_data["phone"],
            activation_consent=form.cleaned_data["activation_consent"],
            customer_inputs={
                key.removeprefix("customer_input_"): request.POST.get(key, "")
                for key in request.POST
                if key.startswith("customer_input_")
            },
            discount_code=form.cleaned_data["discount_code"],
        )
    except UnpaidOrderExists:
        return _page(request, discount_code=code, status=409)
    except ValidationError as exc:
        return _page(
            request,
            discount_code=code,
            error=" ".join(exc.messages),
            status=409 if getattr(exc, "code", None) == "quote_changed" else 400,
        )
    return redirect("order-detail", order_number=order.order_number)

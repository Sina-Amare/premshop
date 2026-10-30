import uuid

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.forms import CodeForm, EmailForm, PasswordLoginForm
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
    purchase = quote.purchase if quote else None
    if purchase is None and discount_code and request.user.is_authenticated and existing is None:
        try:
            purchase = services.quote_checkout(user=request.user).purchase
        except ValidationError:
            pass
    initial = {
        "checkout_id": uuid.uuid4(),
        "quote": quote.token if quote else "",
        "discount_code": discount_code,
        "phone": request.POST.get("phone", "")[:32],
    }
    if purchase:
        for line in purchase.lines:
            key = f"customer_input_{line.plan.pk}"
            initial[key] = request.POST.get(key, "")[:2000]
    form = CheckoutForm(initial=initial, purchase=purchase)
    login_method = "password" if request.GET.get("method") == "password" else "code"
    password_form = PasswordLoginForm()
    if login_method != "password":
        password_form.fields["password"].widget.attrs["disabled"] = True
    if request.method == "POST" and request.POST.get("action") == "checkout" and error:
        submitted = CheckoutForm(request.POST, purchase=purchase)
        submitted.is_valid()
        # Keep field errors while presenting a new quote.
        form.errors.update(submitted.errors)
        for _, bound in form.input_fields:
            if bound.errors:
                bound.field.widget.attrs["aria-describedby"] += f" {bound.auto_id}_error"
    return render(
        request,
        "payments/checkout.html",
        {
            "quote": quote,
            "purchase": purchase,
            "existing": existing,
            "error": error,
            "form": form,
            "cart": cart_summary(get_cart(request)),
            "email_form": EmailForm(),
            "code_form": CodeForm(),
            "password_form": password_form,
            "login_method": login_method,
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
    if action in ("review", "apply_discount", "remove_discount"):
        if action == "remove_discount":
            code = ""
        if action == "apply_discount" and not code.strip():
            return _page(request, error="کد تخفیف را وارد کنید.", status=400)
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

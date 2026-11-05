from typing import Any, cast

from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.orders.admin import OrderAdmin
from apps.orders.models import Order
from apps.orders.services import can_pay_order
from apps.payments.forms import ManualPaymentForm
from apps.payments.services import record_manual_payment

# Keep the payment presentation downstream of orders, like its services.
admin.site.unregister(Order)


@admin.register(Order)
class PaymentOrderAdmin(OrderAdmin):
    def get_urls(self) -> Any:
        return [
            path(
                "<int:order_id>/payment/",
                self.admin_site.admin_view(self.record_payment_view),
                name="orders_order_record_manual_payment",
            ),
        ] + super().get_urls()

    def can_record_payment(self, request: HttpRequest) -> bool:
        return self.has_view_permission(request) and request.user.has_perms(
            ["payments.add_payment", "orders.change_order", "orders.change_orderitem"]
        )

    def record_payment_view(self, request: HttpRequest, order_id: int) -> HttpResponse:
        if not self.can_record_payment(request):
            raise PermissionDenied
        if request.method not in ("GET", "POST"):
            return HttpResponseNotAllowed(["GET", "POST"])
        order = get_object_or_404(self.get_queryset(request), pk=order_id)
        form = ManualPaymentForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                record_manual_payment(
                    order_id=order.pk,
                    actor=cast(User, request.user),
                    amount=form.cleaned_data["amount"],
                    bank_ref=form.cleaned_data["bank_ref"],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                self.message_user(request, _("پرداخت ثبت شد."))
                return redirect("admin:orders_order_change", order.pk)
        return render(
            request,
            "admin/orders/manual_payment.html",
            {
                **self.admin_site.each_context(request),
                "title": _("ثبت پرداخت خارج از درگاه"),
                "opts": self.opts,
                "order": order,
                "form": form,
            },
            status=400 if form.is_bound else 200,
        )

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Order | None = None,
    ) -> Any:
        if obj:
            context["can_record_payment"] = self.can_record_payment(request) and can_pay_order(obj)
            if request.user.has_perm("payments.view_payment"):
                context["payment_history"] = obj.payments.select_related("matched_by")
        return super().render_change_form(request, context, add, change, form_url, obj)

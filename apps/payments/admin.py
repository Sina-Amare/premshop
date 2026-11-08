from typing import Any, cast

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from apps.accounts.models import User
from apps.core.formatting import format_jalali
from apps.orders.admin import OrderAdmin
from apps.orders.models import Order
from apps.orders.services import can_pay_order
from apps.payments.forms import ManualPaymentForm, ReconciliationForm, RefundTransferForm
from apps.payments.models import Refund
from apps.payments.selectors import reconciliation, reference_matches
from apps.payments.services import execute_refunds, record_manual_payment

# Keep the payment presentation downstream of orders, like its services.
admin.site.unregister(Order)


@admin.register(Order)
class PaymentOrderAdmin(OrderAdmin):
    def get_urls(self) -> Any:
        return [
            path(
                "reconciliation/",
                self.admin_site.admin_view(self.reconciliation_view),
                name="orders_order_reconciliation",
            ),
            path(
                "<int:order_id>/refund/",
                self.admin_site.admin_view(self.refund_transfer_view),
                name="orders_order_execute_refunds",
            ),
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

    def viewable_orders(self, request: HttpRequest) -> QuerySet[Order]:
        orders = self.get_queryset(request)
        return orders.filter(
            pk__in=[order.pk for order in orders if self.has_view_permission(request, order)]
        )

    def warn_reference(self, request: HttpRequest, reference: str, order_id: int) -> None:
        if request.user.has_perms(
            ["payments.view_payment", "payments.view_refund"]
        ) and reference_matches(reference, order_id, orders=self.viewable_orders(request)):
            self.message_user(
                request,
                _("این شماره‌ی پیگیری در سفارش دیگری هم ثبت شده است؛ آن را با بانک تطبیق دهید."),
                messages.WARNING,
            )

    @method_decorator(sensitive_post_parameters())
    @method_decorator(sensitive_variables())
    def record_payment_view(self, request: HttpRequest, order_id: int) -> HttpResponse:
        if not self.can_record_payment(request):
            raise PermissionDenied
        if request.method not in ("GET", "POST"):
            return HttpResponseNotAllowed(["GET", "POST"])
        order = get_object_or_404(self.get_queryset(request), pk=order_id)
        if not self.has_view_permission(request, order):
            raise PermissionDenied
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
                self.warn_reference(request, form.cleaned_data["bank_ref"], order.pk)
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

    def can_execute_refunds(self, request: HttpRequest) -> bool:
        return self.has_view_permission(request) and request.user.has_perms(
            [
                "payments.view_refund",
                "payments.change_refund",
                "orders.change_order",
                "orders.change_orderitem",
            ]
        )

    @method_decorator(sensitive_post_parameters())
    @method_decorator(sensitive_variables())
    def refund_transfer_view(self, request: HttpRequest, order_id: int) -> HttpResponse:
        if not self.can_execute_refunds(request):
            raise PermissionDenied
        if request.method not in ("GET", "POST"):
            return HttpResponseNotAllowed(["GET", "POST"])
        order = get_object_or_404(self.get_queryset(request), pk=order_id)
        if not self.has_view_permission(request, order):
            raise PermissionDenied
        refunds = list(
            Refund.objects.filter(order_item__order=order)
            .select_related("order_item")
            .defer("order_item__customer_input")
        )
        form = RefundTransferForm(
            request.POST if request.method == "POST" else None, refunds=refunds
        )
        if request.method == "POST" and form.is_valid():
            try:
                execute_refunds(
                    order_id=order.pk,
                    actor=cast(User, request.user),
                    **{
                        key: form.cleaned_data[key]
                        for key in (
                            "refund_ids",
                            "amount",
                            "destination_kind",
                            "destination_last4",
                            "bank_ref",
                            "executed_at",
                        )
                    },
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                self.message_user(request, _("واریز بازپرداخت ثبت شد."))
                self.warn_reference(request, form.cleaned_data["bank_ref"], order.pk)
                return redirect("admin:orders_order_change", order.pk)
        return render(
            request,
            "admin/orders/manual_payment.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.opts,
                "order": order,
                "title": _("ثبت واریز بازپرداخت"),
                "form": form,
                "refund_transfer": True,
                "has_refunds": any(row.status == Refund.Status.OWED for row in refunds),
            },
            status=400 if form.is_bound else 200,
        )

    def reconciliation_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_view_permission(request) or not request.user.has_perms(
            ["payments.view_payment", "payments.view_refund"]
        ):
            raise PermissionDenied
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        form = ReconciliationForm(
            request.GET
            if "day" in request.GET
            else {"day": format_jalali(timezone.localdate(), with_month_name=False)}
        )
        report = (
            reconciliation(form.cleaned_data["day"], orders=self.viewable_orders(request))
            if form.is_valid()
            else {}
        )
        return render(
            request,
            "admin/orders/reconciliation.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.opts,
                "title": _("تطبیق روزانه"),
                "form": form,
                **report,
            },
            status=400 if form.errors else 200,
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
            context["can_execute_refunds"] = (
                self.can_execute_refunds(request)
                and Refund.objects.filter(order_item__order=obj, status=Refund.Status.OWED).exists()
            )
            if request.user.has_perm("payments.view_refund"):
                context["refund_history"] = Refund.objects.filter(
                    order_item__order=obj
                ).select_related("executed_by")
            context["can_record_payment"] = self.can_record_payment(request) and can_pay_order(obj)
            if request.user.has_perm("payments.view_payment"):
                context["payment_history"] = obj.payments.select_related("matched_by")
        return super().render_change_form(request, context, add, change, form_url, obj)

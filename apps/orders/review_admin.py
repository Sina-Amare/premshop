from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import URLPattern, path
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST, require_safe
from unfold.admin import ModelAdmin

from apps.accounts.models import User
from apps.orders.models import Review
from apps.orders.review_moderation import HideReason, moderate_review


class ReviewAdmin(ModelAdmin):
    def has_view_permission(self, request: HttpRequest, obj: Review | None = None) -> bool:
        return request.user.has_perm("orders.view_review")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Review | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Review | None = None) -> bool:
        return False

    def get_urls(self) -> list[URLPattern]:
        wrap = self.admin_site.admin_view
        return [
            path("", wrap(self.changelist_view), name="orders_review_changelist"),
            path("add/", wrap(self.add_view), name="orders_review_add"),
            path("<int:review_id>/change/", wrap(self.detail), name="orders_review_change"),
            path("<int:review_id>/history/", wrap(self.history), name="orders_review_history"),
            path("<int:review_id>/moderate/", wrap(self.moderate), name="orders_review_moderate"),
        ]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Review]:
        return (
            super()
            .get_queryset(request)
            .select_related("order_item__plan__product")
            .only("status", "text", "rating", "created_at", "order_item__plan__product__name")
        )

    @method_decorator(require_safe)
    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, str] | None = None
    ) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied
        status = request.GET.get("status", Review.Status.PENDING)
        if status not in Review.Status.values:
            raise Http404
        ordering = (
            ("created_at", "pk") if status == Review.Status.PENDING else ("-created_at", "-pk")
        )
        queue = self.get_queryset(request).defer("text").filter(status=status).order_by(*ordering)
        page = Paginator(queue, 25).get_page(request.GET.get("page"))
        return render(
            request,
            "admin/orders/review_queue.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.opts,
                "title": self.opts.verbose_name_plural,
                "statuses": Review.Status.choices,
                "status": status,
                "page": page,
            },
        )

    @method_decorator(require_safe)
    def detail(self, request: HttpRequest, review_id: int) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied
        review = get_object_or_404(self.get_queryset(request), pk=review_id)
        return render(
            request,
            "admin/orders/review_detail.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.opts,
                "title": self.opts.verbose_name,
                "original": review,
                "review": review,
                "can_moderate": request.user.has_perm("orders.change_review"),
                "hide_reasons": HideReason.choices,
            },
        )

    @method_decorator(require_safe)
    def history(self, request: HttpRequest, review_id: int) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied
        return cast(
            HttpResponse, self.history_view(request, str(review_id), {"review_history": True})
        )

    @method_decorator(require_POST)
    def moderate(self, request: HttpRequest, review_id: int) -> HttpResponse:
        if not self.has_view_permission(request) or not request.user.has_perm(
            "orders.change_review"
        ):
            raise PermissionDenied
        decision = request.POST.get("decision", "")
        try:
            moderate_review(
                review_id=review_id,
                actor=cast(User, request.user),
                expected_status=request.POST.get("expected_status", ""),
                decision=decision,
                reason=request.POST.get("reason", ""),
            )
        except Review.DoesNotExist as exc:
            raise Http404 from exc
        except ValidationError as exc:
            self.message_user(request, exc.messages[0], level=messages.ERROR)
        else:
            message = _("نظر منتشر شد.") if decision == "publish" else _("نظر پنهان شد.")
            self.message_user(request, str(message), level=messages.SUCCESS)
        return redirect("admin:orders_review_change", review_id)

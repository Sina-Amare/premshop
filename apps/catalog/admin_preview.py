"""Validate an editor submission and render its in-memory storefront snapshot."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, cast

from django.contrib.admin.utils import flatten_fieldsets
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import UploadedFile
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext as _
from unfold.admin import ModelAdmin

from apps.catalog.models import Plan, Product
from apps.catalog.placements import field_placements
from apps.catalog.views import _for_cards, product_context
from apps.core.context import shell


@dataclass
class UploadedPreview:
    url: str


def preview_image(instance: Any, name: str, value: Any) -> None:
    if isinstance(value, UploadedFile):
        value.seek(0)
        encoded = base64.b64encode(value.read()).decode("ascii")
        value.seek(0)
        mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[
            cast(Any, value).image.format
        ]
        setattr(instance, name, UploadedPreview(f"data:{mime};base64,{encoded}"))


class PreviewAdminMixin(ModelAdmin):
    warn_unsaved_form = True
    """Use the same permitted forms and inlines as the normal change page."""

    def get_urls(self) -> Any:
        name = self.opts.model_name
        routes = [
            path(
                "<path:object_id>/preview/",
                self.admin_site.admin_view(self.preview_view),
                name=f"catalog_{name}_preview",
            )
        ]
        if name != "plan":
            routes.insert(
                0,
                path(
                    "preview/",
                    self.admin_site.admin_view(self.preview_view),
                    name=f"catalog_{name}_add_preview",
                ),
            )
        return routes + super().get_urls()

    def render_change_form(self, request: Any, context: Any, *args: Any, **kwargs: Any) -> Any:
        obj = kwargs.get("obj") or context.get("original")
        context["snapshot_url"] = reverse(
            f"admin:catalog_{self.opts.model_name}_{'preview' if obj else 'add_preview'}",
            args=[obj.pk] if obj else [],
        )
        context["field_placements"] = field_placements(self.opts.model_name)
        context["snapshot_text"] = {
            "open": _("دیدن در پیش‌نمایش"),
            "stale": _("فرم تغییر کرده؛ برای دیدن نسخه‌ی جدید، پیش‌نمایش رو به‌روز کنید."),
            "busy": _("در حال آماده‌کردن پیش‌نمایش…"),
            "fresh": _("این نسخه از فرم آماده‌ست. هنوز چیزی ذخیره نشده."),
            "failed": _("پیش‌نمایش به‌روز نشد. نسخه‌ی قبلی همچنان نمایش داده می‌شه."),
            "absent": _("این بخش با اطلاعات فعلی نمایش داده نمی‌شه. راهنمای کنار فیلد رو ببینید."),
        }
        return super().render_change_form(request, context, *args, **kwargs)

    def preview_view(self, request: HttpRequest, object_id: str | None = None) -> HttpResponse:
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        obj = self.get_object(request, object_id) if object_id else None
        if object_id and obj is None:
            raise Http404
        if not (
            self.has_change_permission(request, obj) if obj else self.has_add_permission(request)
        ):
            raise PermissionDenied
        form_class = self.get_form(
            request,
            obj,
            change=obj is not None,
            fields=flatten_fieldsets(self.get_fieldsets(request, obj)),
        )
        form = form_class(request.POST, request.FILES, instance=obj)
        formsets, inlines = self._create_formsets(request, form.instance, change=obj is not None)
        valid = form.is_valid()
        for formset in formsets:
            allowed_ids = {str(row.pk) for row in formset.get_queryset()}
            for row_form in formset.forms:
                submitted_id = row_form.data.get(row_form.add_prefix("id"))
                if submitted_id and submitted_id not in allowed_ids:
                    row_form.add_error(None, _("این ردیف متعلق به این محصول نیست."))
            valid = formset.is_valid() and valid
        errors: list[dict[str, Any]] = []
        for bound in [form, *(row for formset in formsets for row in formset.forms)]:
            errors.extend(
                {
                    "field": bound.add_prefix(field),
                    "id": (
                        (bound.prefix or f"{self.opts.model_name}_form")
                        if field == "__all__"
                        else "id_" + bound.add_prefix(field)
                    ),
                    "messages": messages,
                }
                for field, messages in bound.errors.items()
            )
        errors.extend(
            {
                "field": formset.prefix,
                "id": formset.prefix + "-group",
                "messages": formset.non_form_errors(),
            }
            for formset in formsets
            if formset.non_form_errors()
        )
        if not valid:
            response = render(
                request, "admin/catalog/preview_errors.html", {"errors": errors}, status=422
            )
        else:
            snapshot = form.instance
            if isinstance(snapshot, Product):
                for formset, inline in zip(formsets, inlines, strict=True):
                    rows = []
                    last = max((row.sort_order for row in formset.get_queryset()), default=-1)
                    for index, row_form in enumerate(formset.forms):
                        if not formset.is_kept(row_form):
                            continue
                        row = row_form.instance
                        if row.pk is None:
                            if "sort_order" not in row_form.changed_data:
                                last += 1
                                row.sort_order = last
                            row.pk = -(index + 1)
                        row.preview_key = row_form.prefix
                        for name, value in row_form.cleaned_data.items():
                            if name == "image":
                                preview_image(row, name, value)
                        rows.append(row)
                    rows.sort(key=lambda row: (row.sort_order, row.pk))
                    snapshot.__dict__.setdefault("_prefetched_objects_cache", {})[
                        inline.model._meta.default_related_name
                        or formset.fk.remote_field.related_name
                    ] = rows
                for relation in ("plans", "specs", "sections", "questions"):
                    cache = snapshot.__dict__.setdefault("_prefetched_objects_cache", {})
                    if relation not in cache:
                        cache[relation] = (
                            list(getattr(snapshot, relation).all()) if snapshot.pk else []
                        )
                preview_image(snapshot, "image", form.cleaned_data.get("image"))
                if snapshot.pk is None:
                    snapshot.pk = -1
                context = product_context(snapshot)
                template = "catalog/product.html"
            elif isinstance(snapshot, Plan):
                parent_admin = self.admin_site._registry[Product]
                parent = parent_admin.get_object(request, str(snapshot.product_id))
                if parent is None or not parent_admin.has_view_or_change_permission(
                    request, parent
                ):
                    raise PermissionDenied
                parent = (
                    _for_cards(Product.objects.all())
                    .prefetch_related("specs", "sections", "questions")
                    .get(pk=parent.pk)
                )
                parent.__dict__["_prefetched_objects_cache"]["plans"] = sorted(
                    [row for row in parent.plans.all() if row.pk != snapshot.pk] + [snapshot],
                    key=lambda row: (row.sort_order, row.pk),
                )
                cast(Any, snapshot).preview_key = "plan"
                context = product_context(parent)
                template = "catalog/product.html"
            else:
                products = list(_for_cards().filter(category_id=snapshot.pk)) if snapshot.pk else []
                for product in products:
                    product.category = snapshot
                navigation = cast(list[Any], shell(request)["nav_categories"])
                navigation = [snapshot if cat.pk == snapshot.pk else cat for cat in navigation]
                navigation.sort(key=lambda cat: (cat.sort_order, cat.name))
                context = {
                    "category": snapshot,
                    "products": products,
                    "current_category": snapshot.slug,
                    "quick_plan_products": products,
                    "nav_categories": navigation,
                }
                template = "catalog/category.html"
            context.update(snapshot=True, preview=False)
            response = render(request, template, context)
        patch_cache_control(response, private=True, no_store=True)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

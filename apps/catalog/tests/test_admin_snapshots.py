"""Unsaved previews validate the editor without changing its stored catalog."""

import pytest
from django.contrib.admin.models import LogEntry
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.catalog.models import Category, Product, ProductSection, ProductSpec
from apps.catalog.tests.test_admin import add_row, form_of
from apps.catalog.tests.test_admin import catalogue as catalogue
from apps.catalog.tests.test_admin import operator as operator

pytestmark = pytest.mark.django_db


def test_product_snapshot_uses_posted_values_without_writing(client, operator, catalogue):
    obj = catalogue["draft"]
    data = form_of(client, reverse("admin:catalog_product_change", args=[obj.pk]))
    data["name"] = "Snapshot service"
    data["brand_colour"] = "#ff2200"
    data["plans-0-sale_price"] = "345678"
    before = LogEntry.objects.count()
    with CaptureQueriesContext(connection) as queries:
        response = client.post(reverse("admin:catalog_product_preview", args=[obj.pk]), data)
    assert response.status_code == 200
    body = response.content.decode()
    assert "Snapshot service" in body
    assert "۳۴۵,۶۷۸" in body
    assert "--brand: #ff2200" in body
    assert 'data-preview-target="card"' in body
    assert not any(
        q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries
    )
    obj.refresh_from_db()
    assert obj.name == "draft"
    assert LogEntry.objects.count() == before
    assert "no-store" in response["Cache-Control"]
    assert response["X-Robots-Tag"] == "noindex, nofollow"


def test_invalid_snapshot_has_linked_errors_and_does_not_create(client, operator, catalogue):
    data = form_of(client, reverse("admin:catalog_product_change", args=[catalogue["draft"].pk]))
    data["brand_colour"] = "wrong"
    response = client.post(
        reverse("admin:catalog_product_preview", args=[catalogue["draft"].pk]), data
    )
    assert response.status_code == 422
    assert 'data-field="id_brand_colour"' in response.content.decode()
    assert Product.objects.count() == 3


def test_snapshot_requires_object_permission_and_csrf(client, operator, catalogue):
    url = reverse("admin:catalog_product_preview", args=[catalogue["draft"].pk])
    assert client.get(url).status_code == 405
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(operator)
    assert protected.post(url, {}).status_code == 403
    operator.is_superuser = False
    operator.save()
    assert client.post(url, {}).status_code == 403


@pytest.mark.parametrize("model", ["product", "category"])
def test_new_object_can_be_previewed_without_creation(client, operator, catalogue, model):
    data = form_of(client, reverse(f"admin:catalog_{model}_add"), f"{model}_form")
    data.update(
        name="New preview",
        slug="new-preview",
        category=str(catalogue["draft"].category_id),
        delivery_type="ready_account",
        status="draft",
    )
    response = client.post(reverse(f"admin:catalog_{model}_add_preview"), data)
    assert response.status_code == 200, response.content.decode()
    assert "New preview" in response.content.decode()
    assert not Product.objects.filter(slug="new-preview").exists()
    assert not Category.objects.filter(slug="new-preview").exists()


def test_category_snapshot_overlays_eligible_navigation_without_new_products(
    client, operator, catalogue
):
    category = catalogue["live"].category
    data = form_of(
        client, reverse("admin:catalog_category_change", args=[category.pk]), "category_form"
    )
    data["name"] = "Unsaved category"
    response = client.post(reverse("admin:catalog_category_preview", args=[category.pk]), data)
    assert response.status_code == 200
    body = response.content.decode()
    assert "Unsaved category" in body
    assert 'href="/p/live/"' in body
    assert 'href="/p/draft/"' not in body
    category.refresh_from_db()
    assert category.name != "Unsaved category"


def test_plan_snapshot_uses_parent_permissions_and_never_exposes_internal_values(
    client, operator, catalogue, monkeypatch
):
    from django.contrib import admin

    plan = catalogue["draft"].plans.get()
    data = form_of(client, reverse("admin:catalog_plan_change", args=[plan.pk]), "plan_form")
    data.update(
        title="Special preview plan",
        cost_price="123456789",
        supplier_url="https://private-supplier.example/secret",
    )
    url = reverse("admin:catalog_plan_preview", args=[plan.pk])
    response = client.post(url, data)
    assert response.status_code == 200
    body = response.content.decode()
    assert "Special preview plan" in body
    assert "123456789" not in body and "۱۲۳,۴۵۶,۷۸۹" not in body and "private-supplier" not in body
    plan.refresh_from_db()
    assert plan.title != "Special preview plan"
    monkeypatch.setattr(
        admin.site._registry[Product], "has_view_or_change_permission", lambda *args: False
    )
    assert client.post(url, data).status_code == 403


def test_snapshot_retains_adds_deletes_and_reorders_rows_without_saving(
    client, operator, catalogue
):
    obj = catalogue["draft"]
    ProductSpec.objects.create(product=obj, title="Keep me", value="First", sort_order=0)
    ProductSpec.objects.create(product=obj, title="Remove me", value="Second", sort_order=1)
    ProductSection.objects.create(
        product=obj, kind="free", title="Saved content", body="Existing text"
    )
    data = form_of(client, reverse("admin:catalog_product_change", args=[obj.pk]))
    data["specs-1-DELETE"] = "on"
    data["specs-0-sort_order"] = "4"
    # Use an existing empty extra row for the new specification.
    data.update(
        {"specs-2-title": "New spec", "specs-2-value": "Unsaved value", "specs-2-sort_order": "3"}
    )
    prefix = add_row(data, "sections", kind="free", title="New content", body="New text")
    response = client.post(reverse("admin:catalog_product_preview", args=[obj.pk]), data)
    assert response.status_code == 200, response.content.decode()
    body = response.content.decode()
    assert body.index("New spec") < body.index("Keep me")
    assert "Remove me" not in body and "Saved content" in body and "New content" in body
    assert f'data-preview-row="{prefix}"' in body
    assert obj.specs.count() == 2 and obj.sections.count() == 1
    other = catalogue["live"].plans.get()
    data["plans-0-id"] = str(other.pk)
    assert (
        client.post(reverse("admin:catalog_product_preview", args=[obj.pk]), data).status_code
        == 422
    )


def test_snapshot_validates_images_in_memory_without_storage_writes(
    client, operator, catalogue, settings, tmp_path
):
    from io import BytesIO

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    settings.MEDIA_ROOT = tmp_path
    obj = catalogue["draft"]
    data = form_of(client, reverse("admin:catalog_product_change", args=[obj.pk]))
    image = BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, format="PNG")
    data["image"] = SimpleUploadedFile("mark.png", image.getvalue(), content_type="image/png")
    response = client.post(reverse("admin:catalog_product_preview", args=[obj.pk]), data)
    assert response.status_code == 200, response.content.decode()
    assert "data:image/png;base64," in response.content.decode()
    assert list(tmp_path.iterdir()) == []
    obj.refresh_from_db()
    assert not obj.image
    data["image"] = SimpleUploadedFile(
        "bad.svg", b'<svg onload="alert(1)"/>', content_type="image/svg+xml"
    )
    assert (
        client.post(reverse("admin:catalog_product_preview", args=[obj.pk]), data).status_code
        == 422
    )


def test_every_editable_catalog_field_has_a_placement_or_behavior_note(client, operator, catalogue):
    from django.contrib import admin
    from django.test import RequestFactory

    from apps.catalog.models import Plan
    from apps.catalog.placements import field_placements

    request = RequestFactory().get("/admin/")
    request.user = operator
    for model in (Product, Plan, Category):
        panel = admin.site._registry[model]
        obj = (
            catalogue["draft"]
            if model is Product
            else catalogue["draft"].plans.get() if model is Plan else catalogue["draft"].category
        )
        mapping = field_placements(model._meta.model_name)
        fields = panel.get_form(request, obj).base_fields
        assert all(f"{model._meta.model_name}.{name}" in mapping for name in fields)
        if model is Product:
            for inline in panel.get_inline_instances(request, obj):
                prefix = inline.get_formset(request, obj).get_default_prefix()
                fields = inline.get_formset(request, obj).form.base_fields
                assert all(
                    f"{prefix}.{name}" in mapping
                    for name in fields
                    if name not in {"id", "product", "DELETE"}
                )


@pytest.mark.parametrize("move", [False, True])
def test_standalone_plan_snapshot_honors_parent_and_order_changes(
    client, operator, catalogue, move
):
    from apps.catalog.models import Plan

    original = catalogue["draft"].plans.get()
    destination = catalogue["live"] if move else catalogue["draft"]
    if not move:
        Plan.objects.create(
            product=destination,
            title="Existing first",
            sort_order=0,
            cost_price=1,
            sale_price=150000,
        )
    data = form_of(client, reverse("admin:catalog_plan_change", args=[original.pk]), "plan_form")
    data.update(product=str(destination.pk), sort_order="5", title="Submitted last")
    response = client.post(reverse("admin:catalog_plan_preview", args=[original.pk]), data)
    assert response.status_code == 200
    plans = response.context["plans"]
    assert plans[-1].pk == original.pk
    assert plans[-1].title == "Submitted last"
    assert len(plans) == 2
    original.refresh_from_db()
    assert original.product_id == catalogue["draft"].pk and original.title != "Submitted last"

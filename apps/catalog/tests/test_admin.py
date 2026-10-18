"""The operator panel, which had no tests and had already broken in silence.

The thumbnail column shipped rendering `file:///C:/...` into every `<img src>`
because Unfold's header display reads an attribute called `path` and uses it
as a URL. The server returned 200 the whole time; only a browser console said
otherwise. Everything here exists to make that class of failure loud:

- a value that must *resolve* to something, not merely be set (the image URL)
- a lookup that falls through to a default instead of raising (the status
  badge, keyed off labels that a rename would quietly move)
- Persian that a Django or Unfold upgrade could revert to English
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(client):
    staff = User.objects.create_user("operator@example.test", "x", is_staff=True)
    staff.is_superuser = True
    staff.save()
    client.force_login(staff)
    return staff


@pytest.fixture
def catalogue():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    made = {}
    for slug, status in [
        ("live", Product.Status.ACTIVE),
        ("draft", Product.Status.DRAFT),
        ("gone", Product.Status.UNAVAILABLE),
    ]:
        product = Product.objects.create(
            category=category,
            name=slug,
            slug=slug,
            delivery_type=Product.DeliveryType.READY_ACCOUNT,
            status=status,
        )
        Plan.objects.create(
            product=product,
            title="۱ ماهه",
            cost_price=Decimal("100"),
            sale_price=Decimal("200000"),
        )
        made[slug] = product
    return made


def test_the_product_list_renders_for_an_operator(client, operator, catalogue):
    response = client.get(reverse("admin:catalog_product_changelist"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "live" in body and "draft" in body


def test_every_status_keeps_its_own_colour(client, operator, catalogue):
    """Unfold resolves the badge with `value in label` and falls through to
    neutral grey when it misses. Keying the map off Persian strings meant a
    rename in models.py would grey every badge with nothing raising, so the
    map is keyed off the enum and this holds it there."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    for label, colour in [
        (Product.Status.ACTIVE.label, "green"),
        (Product.Status.DRAFT.label, "blue"),
        (Product.Status.UNAVAILABLE.label, "red"),
    ]:
        assert label in body, label
        assert colour in body, f"{label} should render {colour}"


def test_draft_is_not_amber(client, operator, catalogue):
    """ADR-0016 reserves amber for time and nothing else, and Unfold's
    `warning` variant is orange. A draft is not a clock. The only amber in
    this panel belongs to a promotion running out."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    draft_row = body[body.index(Product.Status.DRAFT.label) - 400 :][:500]
    assert "orange" not in draft_row


def test_the_product_mark_is_served_over_http_not_from_the_disk(
    client, operator, catalogue, settings
):
    """The bug this file exists for. Unfold's header display calls the
    attribute `path` and puts it straight into an `<img src>`; handing it
    Django's ImageFieldFile renders `file:///C:/...`, which every browser
    refuses and no server-side assertion would notice."""
    product = catalogue["live"]
    product.image = "products/claude-pro.svg"
    product.save()

    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    sources = re.findall(r'<img[^>]+src="([^"]+)"', body)
    assert sources, "the list renders no images at all"
    assert not [s for s in sources if s.startswith("file:")], sources
    assert any(s.startswith(settings.MEDIA_URL) for s in sources), sources


def test_the_change_form_opens_with_its_inlines(client, operator, catalogue):
    response = client.get(reverse("admin:catalog_product_change", args=[catalogue["live"].pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "spec-title-suggestions" in body, "the spec datalist that keeps wording consistent"
    assert "promo_price" in body, "the plan inline"


def test_the_panel_speaks_persian(client, operator, catalogue):
    """Unfold ships no Persian at all and Django's own is overridden in
    places, so both are a project asset that an upgrade could revert."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    assert "Type to search" not in body
    assert "Filters" not in body
    assert "جستجو" in body
    assert "گذرواژه" not in body, "this shop says «رمز عبور»"


def test_dates_are_jalali(client, operator, catalogue):
    """An operator who works in Mehr and Aban should not have to convert a
    Gregorian month name in their head."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    assert not re.search(r"اکتبر|نوامبر|سپتامبر|ژانویه", body), "a Gregorian month leaked"
    assert re.search(r"مهر|آبان|آذر|دی|بهمن|اسفند|فروردین", body)


def test_groups_are_not_in_the_panel(client, operator):
    """This project has no roles (ADR-0014). A permission screen leading
    nowhere is worse than no screen."""
    body = client.get(reverse("admin:index")).content.decode()

    assert "/admin/auth/group/" not in body

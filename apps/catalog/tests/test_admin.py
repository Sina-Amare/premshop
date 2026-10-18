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
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

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


# --- What the list answers without opening a product -----------------------


def test_a_running_discount_shows_its_time_left(client, operator, catalogue):
    """The catalogue's only real clock. Before this it took opening the
    product and reading two datetime fields to learn a promotion ends
    tonight."""
    plan = catalogue["live"].plans.first()
    plan.promo_price = Decimal("150000")
    plan.promo_ends_at = timezone.now() + timedelta(hours=5)
    plan.save()

    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    assert "۰۴:" in body or "۰۵:" in body, "the hours left are not on the list"


def test_a_product_with_no_discount_shows_no_clock(client, operator, catalogue):
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    assert "—" in body


def test_publishing_from_the_list_writes_the_status(client, operator, catalogue):
    """The row action exists so status can change without opening the form.
    A button that looks like it worked and did not is worse than no button."""
    draft = catalogue["draft"]
    url = reverse("admin:catalog_product_publish", args=[draft.pk])

    response = client.get(url)
    draft.refresh_from_db()

    assert response.status_code in (200, 302)
    assert draft.status == Product.Status.ACTIVE


def test_unpublishing_takes_it_off_the_site(client, operator, catalogue):
    live = catalogue["live"]
    client.get(reverse("admin:catalog_product_unpublish", args=[live.pk]))
    live.refresh_from_db()

    assert live.status == Product.Status.DRAFT
    assert live not in Product.objects.public()


def test_the_plan_list_shows_what_each_plan_earns(client, operator, catalogue):
    """`cost_price` says it is «برای حساب سود» and nothing computed that sum
    until this page. The figure is the effective price, so a running
    discount is counted rather than the list quoting a profit nobody makes."""
    plan = catalogue["live"].plans.first()

    body = client.get(reverse("admin:catalog_plan_changelist")).content.decode()

    assert response_has_margin(body), body[:200]
    assert plan.product.name in body


def response_has_margin(body: str) -> bool:
    return "٪" in body


def test_a_plan_cannot_be_created_without_its_product(client, operator):
    """A plan with no product prices nothing. It is added on the product."""
    assert client.get(reverse("admin:catalog_plan_add")).status_code in (403, 302)

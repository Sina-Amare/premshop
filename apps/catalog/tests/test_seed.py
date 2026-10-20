"""seed_catalog fills an empty catalogue and never rewrites one that is already there."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.catalog.management.commands import seed_catalog
from apps.catalog.models import Category, Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def seed(settings, tmp_path, monkeypatch):
    """The command as the developer runs it, with the network refused.

    A mark for every seeded product is already on disk, so a run that attached
    one to a product it did not create would replace the operator's image.
    """
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "products").mkdir()
    for slug in seed_catalog.MARKS:
        (tmp_path / "products" / f"{slug}.svg").write_bytes(b'<svg viewBox="0 0 1 1"></svg>')

    def offline(*args, **kwargs):
        raise OSError("no network in tests")

    monkeypatch.setattr(seed_catalog.urllib.request, "urlopen", offline)
    return lambda: call_command("seed_catalog", stdout=StringIO(), stderr=StringIO())


def test_an_empty_database_gets_the_whole_mock_shop(seed):
    seed()

    assert Product.objects.count() == len(seed_catalog.PRODUCTS)
    chatgpt = Product.objects.get(slug="chatgpt-plus")
    assert chatgpt.plans.count() == 1
    assert chatgpt.image.name == "products/chatgpt-plus.svg"


def test_a_second_run_keeps_the_operators_edits_and_every_plan(seed):
    """It deleted and recreated every seeded product's plans and specs and wrote
    its own text and mark back over the operator's. From S4a an order line
    holds its plan by PROTECT, so a sold plan's row must outlive any re-seed."""
    seed()
    product = Product.objects.get(slug="chatgpt-plus")
    product.short_description = "متن خود اپراتور."
    product.image.name = "products/uploaded.png"
    product.save()
    category = product.category
    category.description = "مقدمه‌ی خود اپراتور."
    category.save()
    plans = list(product.plans.values_list("pk", "sale_price"))
    specs = list(product.specs.values_list("pk", flat=True))

    seed()

    product.refresh_from_db()
    category.refresh_from_db()
    assert product.short_description == "متن خود اپراتور."
    assert product.image.name == "products/uploaded.png"
    assert category.description == "مقدمه‌ی خود اپراتور."
    assert list(product.plans.values_list("pk", "sale_price")) == plans
    assert list(product.specs.values_list("pk", flat=True)) == specs
    assert Product.objects.count() == len(seed_catalog.PRODUCTS)
    assert Category.objects.count() == len(seed_catalog.CATEGORIES)

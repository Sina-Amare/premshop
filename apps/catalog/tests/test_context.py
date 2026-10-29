from unittest.mock import patch

import pytest

from apps.catalog.models import Category, Product


@pytest.mark.django_db
def test_navigation_has_only_departments_with_public_products(rf, django_assert_num_queries):
    from apps.catalog.context import navigation

    live = Category.objects.create(name="نمایش", slug="shown")
    draft = Category.objects.create(name="پیش‌نویس", slug="hidden")
    Product.objects.create(category=live, name="Live", slug="live", status="active")
    Product.objects.create(category=draft, name="Draft", slug="draft")
    with django_assert_num_queries(1):
        result = navigation(rf.get("/"))
    assert [row.pk for row in result["nav_categories"]] == [live.pk]


def test_navigation_failure_does_not_break_error_rendering(rf):
    from apps.catalog.context import navigation

    with patch.object(
        Category.objects, "annotate", side_effect=RuntimeError("database unavailable")
    ):
        assert navigation(rf.get("/")) == {"nav_categories": []}

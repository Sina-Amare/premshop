"""Adding and removing card colors must leave products and prices intact."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("catalog", "0023_usage_notes")]
AFTER = [("catalog", "0024_product_brand_colour")]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def test_color_migration_preserves_products_plans_and_operator_colors():
    try:
        apps = migrate(BEFORE)
        category = apps.get_model("catalog", "Category").objects.create(name="نمونه", slug="sample")
        product = apps.get_model("catalog", "Product").objects.create(
            category=category, name="Claude Pro", slug="claude-pro", usage_notes="یادداشت اپراتور"
        )
        plan = apps.get_model("catalog", "Plan").objects.create(
            product=product, title="۱ ماهه", cost_price=100, sale_price=200
        )

        apps = migrate(AFTER)
        Product = apps.get_model("catalog", "Product")
        assert Product.objects.get(pk=product.pk).brand_colour == "#d97757"
        Product.objects.filter(pk=product.pk).update(brand_colour="#123456")
        assert apps.get_model("catalog", "Plan").objects.get(pk=plan.pk).sale_price == 200

        apps = migrate(BEFORE)
        assert (
            apps.get_model("catalog", "Product").objects.get(pk=product.pk).usage_notes
            == "یادداشت اپراتور"
        )
        assert apps.get_model("catalog", "Plan").objects.get(pk=plan.pk).sale_price == 200
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

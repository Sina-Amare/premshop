import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_cart_migration_reverses_and_reapplies_preserving_catalog(plan):
    try:
        MigrationExecutor(connection).migrate([("cart", None)])
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('cart_cart'), to_regclass('cart_cartitem')")
            assert cursor.fetchone() == (None, None)
            cursor.execute("SELECT sale_price FROM catalog_plan WHERE id = %s", [plan.pk])
            assert cursor.fetchone()[0] == plan.sale_price
        MigrationExecutor(connection).migrate([("cart", "0001_initial")])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

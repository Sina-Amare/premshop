import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_orders_migrations_reverse_and_reapply_without_touching_catalog(plan):
    plan_id, price = plan.pk, plan.sale_price
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([("orders", None)])
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('orders_order_number_seq'), to_regclass('orders_order')"
            )
            assert cursor.fetchone() == (None, None)
            cursor.execute("SELECT sale_price FROM catalog_plan WHERE id = %s", [plan_id])
            assert cursor.fetchone()[0] == price
        executor = MigrationExecutor(connection)
        executor.migrate([("orders", "0002_protect_history")])
        with connection.cursor() as cursor:
            cursor.execute("SELECT nextval('orders_order_number_seq')")
            assert cursor.fetchone()[0] == 1001
            cursor.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'orders_event_immutable'"
            )
            assert cursor.fetchone()[0] == 1
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

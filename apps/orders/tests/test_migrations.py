from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)


def test_checkout_metadata_backfills_each_legacy_order_without_inventing_consent(buyer):
    previous = ("orders", "0004_align_persian_order_labels")
    ids = []
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        Legacy = executor.loader.project_state([previous]).apps.get_model("orders", "Order")
        created = timezone.now() - timedelta(days=3)
        for _ in range(2):
            row = Legacy.objects.create(user_id=buyer.pk, subtotal=100, total_amount=100)
            ids.append(row.pk)
        Legacy.objects.filter(pk__in=ids).update(created_at=created)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from apps.orders.models import Order

        rows = list(Order.objects.filter(pk__in=ids))
        assert len({row.checkout_id for row in rows}) == 2
        assert all(row.unpaid_expires_at == created + timedelta(hours=24) for row in rows)
        assert all(row.consent_accepted_at is None and row.consent_text == "" for row in rows)
    finally:
        if ids:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM orders_order WHERE id = ANY(%s)", [ids])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


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

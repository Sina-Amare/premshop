import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_payment_migrations_reverse_and_preserve_unpaid_purchase(buyer, plan):
    try:
        executor = MigrationExecutor(connection)
        previous = ("orders", "0012_order_placement_email")
        executor.migrate([previous])
        old = executor.loader.project_state([previous]).apps
        order = old.get_model("orders", "Order").objects.create(
            user_id=buyer.pk, subtotal=200000, total_amount=200000
        )
        item = old.get_model("orders", "OrderItem").objects.create(
            order_id=order.pk,
            plan_id=plan.pk,
            price_snapshot=200000,
            cost_snapshot=100000,
            product_snapshot={"name": "محصول قبلی"},
        )
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from apps.orders.models import OrderItem
        from apps.payments.models import Payment

        assert OrderItem.objects.get(pk=item.pk).paid_at is None
        assert not Payment.objects.exists()
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        with connection.cursor() as cursor:
            cursor.execute("SELECT total_amount FROM orders_order WHERE id=%s", [order.pk])
            assert cursor.fetchone()[0] == 200000
            cursor.execute("SELECT product_snapshot FROM orders_orderitem WHERE id=%s", [item.pk])
            assert "محصول قبلی" in cursor.fetchone()[0]
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

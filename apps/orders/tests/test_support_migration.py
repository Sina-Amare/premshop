import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_support_migration_backfills_independent_encrypted_identifiers_and_reverses(buyer, plan):
    previous = ("orders", "0016_input_waiting_clock")
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        old = executor.loader.project_state([previous]).apps
        Order = old.get_model("orders", "Order")
        Item = old.get_model("orders", "OrderItem")
        orders = [
            Order.objects.create(user_id=buyer.pk, subtotal=200000, total_amount=200000)
            for _ in range(2)
        ]
        item = Item.objects.create(
            order_id=orders[0].pk,
            plan_id=plan.pk,
            price_snapshot=200000,
            cost_snapshot=100000,
            product_snapshot={"name": "نام زمان خرید"},
            customer_input="migration@example.test",
        )
        before_orders = list(Order.objects.order_by("pk").values())
        before_item = Item.objects.filter(pk=item.pk).values().get()
        for _ in range(2):
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            from apps.orders.models import OrderSupportIdentifier

            current = list(OrderSupportIdentifier.objects.order_by("pk"))
            assert len({order.support_token for order in current}) == 2
            assert all(len(order.support_token) == 12 for order in current)
            with connection.cursor() as cursor:
                cursor.execute("SELECT support_token FROM orders_ordersupportidentifier")
                assert all("$" in row[0] and len(row[0]) > 12 for row in cursor.fetchall())
            executor = MigrationExecutor(connection)
            executor.migrate([previous])
            assert list(Order.objects.order_by("pk").values()) == before_orders
            assert Item.objects.filter(pk=item.pk).values().get() == before_item
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_existing_orders_stay_ineligible_and_migrations_reverse(buyer):
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    before = [("notifications", None), ("orders", "0011_order_policy_snapshot")]
    try:
        executor.migrate(before)
        state = executor.loader.project_state([("orders", "0011_order_policy_snapshot")])
        OldOrder = state.apps.get_model("orders", "Order")
        old = OldOrder.objects.create(
            user_id=buyer.pk,
            subtotal=1,
            total_amount=1,
            policy_snapshot={"terms": {"body": "unchanged"}},
        )
        executor = MigrationExecutor(connection)
        executor.migrate(leaves)
        from apps.notifications.services import discover_confirmations
        from apps.orders.models import Order

        row = Order.objects.get(pk=old.pk)
        assert row.placement_email == "" and row.policy_snapshot == {"terms": {"body": "unchanged"}}
        assert discover_confirmations() == 0
    finally:
        MigrationExecutor(connection).migrate(leaves)

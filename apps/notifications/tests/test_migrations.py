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


def test_message_migration_preserves_existing_rows_and_reverses(buyer):
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    before = [("notifications", "0001_initial")]
    try:
        executor.migrate(before)
        state = executor.loader.project_state(before)
        OldOrder = state.apps.get_model("orders", "Order")
        OldNotification = state.apps.get_model("notifications", "Notification")
        order = OldOrder.objects.create(user_id=buyer.pk, subtotal=1, total_amount=1)
        for status, attempts in (("pending", 2), ("sent", 1), ("failed", 6)):
            OldNotification.objects.create(
                order_id=order.pk,
                dedupe_key=f"preserve:{status}",
                recipient="frozen@example.test",
                subject="Saved subject",
                text_body="Saved text",
                html_body="<p>Saved HTML</p>",
                status=status,
                attempts=attempts,
                last_error_class="SMTPDataError",
                smtp_code=550,
            )
        saved = list(OldNotification.objects.order_by("pk").values())
        MigrationExecutor(connection).migrate(leaves)
        from apps.notifications.models import Notification

        assert set(Notification.objects.values_list("template_name", flat=True)) == {
            "purchase_confirmation"
        }
        current = list(Notification.objects.order_by("pk").values())
        assert [{k: v for k, v in row.items() if k != "template_name"} for row in current] == saved
        MigrationExecutor(connection).migrate(before)
        assert list(OldNotification.objects.order_by("pk").values()) == saved
    finally:
        MigrationExecutor(connection).migrate(leaves)

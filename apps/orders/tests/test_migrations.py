from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)


def test_waiting_clock_migration_reverses_without_changing_purchase_facts(buyer, plan):
    previous = ("orders", "0015_recorded_delivery_deadlines")
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        old = executor.loader.project_state([previous]).apps
        order = old.get_model("orders", "Order").objects.create(
            user_id=buyer.pk, subtotal=200000, total_amount=200000
        )
        Item = old.get_model("orders", "OrderItem")
        item = Item.objects.create(
            order=order,
            plan_id=plan.pk,
            price_snapshot=200000,
            cost_snapshot=100000,
            product_snapshot={"telegram_required": False},
            customer_input="target@example.test",
        )
        original = Item.objects.filter(pk=item.pk).values().get()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from apps.orders.models import OrderItem

        current = OrderItem.objects.get(pk=item.pk)
        assert current.input_requested_at is None and current.sla_paused_at is None
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        assert Item.objects.filter(pk=item.pk).values().get() == original
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_delivery_clock_backfills_only_recorded_purchase_facts_and_reverses(buyer, plan):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    previous = ("orders", "0013_orderitem_paid_at")
    paid = datetime(2026, 11, 5, 21, tzinfo=ZoneInfo("Asia/Tehran"))
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        old = executor.loader.project_state([previous]).apps
        order = old.get_model("orders", "Order").objects.create(
            user_id=buyer.pk, subtotal=200000, total_amount=200000
        )
        Item = old.get_model("orders", "OrderItem")
        facts = {
            "delivery_hours": 1,
            "requires_customer_input": False,
            "input_type": "none",
            "telegram_required": False,
        }
        rows = []
        for changes in (
            {},
            {"paid_at": None},
            {"product_snapshot": {}},
            {
                "product_snapshot": {
                    **facts,
                    "requires_customer_input": True,
                    "input_type": "email",
                },
                "customer_input": "target@example.test",
            },
            {"product_snapshot": {**facts, "requires_customer_input": True, "input_type": "email"}},
        ):
            rows.append(
                Item.objects.create(
                    **{
                        "order_id": order.pk,
                        "plan_id": plan.pk,
                        "price_snapshot": 200000,
                        "cost_snapshot": 100000,
                        "product_snapshot": facts,
                        "paid_at": paid,
                        **changes,
                    }
                )
            )
        Item.objects.filter(pk=rows[3].pk).update(created_at=paid - timedelta(hours=2))
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, customer_input FROM orders_orderitem ORDER BY id")
            ciphertext = cursor.fetchall()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from apps.orders.models import OrderItem

        result = [OrderItem.objects.defer("customer_input").get(pk=row.pk) for row in rows]
        expected = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo("Asia/Tehran"))
        assert result[0].due_at == expected and result[0].input_received_at is None
        assert result[1].due_at is None and result[2].due_at is None
        assert result[3].input_received_at == paid - timedelta(hours=2)
        assert result[3].due_at == expected
        assert result[4].input_received_at is None and result[4].due_at is None
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, customer_input FROM orders_orderitem ORDER BY id")
            assert cursor.fetchall() == ciphertext
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_policy_snapshot_migration_keeps_older_purchase_facts_without_backfill(buyer, plan):
    previous = ("orders", "0010_credential_access_log")
    facts = {"name": "محصول قبلی", "plan_title": "پلن قبلی", "refund_note": "شرایط قبلی"}
    try:
        executor = MigrationExecutor(connection)
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
            product_snapshot=facts,
            customer_input="stored@example.test",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT customer_input FROM orders_orderitem WHERE id = %s", [item.pk])
            ciphertext = cursor.fetchone()[0]
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from apps.orders.models import Order, OrderItem

        assert Order.objects.get(pk=order.pk).policy_snapshot == {}
        assert OrderItem.objects.get(pk=item.pk).product_snapshot == facts
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        with connection.cursor() as cursor:
            cursor.execute("SELECT customer_input FROM orders_orderitem WHERE id = %s", [item.pk])
            assert cursor.fetchone() == (ciphertext,)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_access_log_reverses_and_reapplies_without_changing_encrypted_input(buyer, plan):
    from apps.accounts.models import User
    from apps.catalog.models import Plan
    from apps.orders.models import CredentialAccessLog, OrderItem
    from apps.orders.services import OrderLine, place_order, reveal_customer_input

    plan.input_type = Plan.InputType.EMAIL
    plan.max_quantity = 1
    plan.save()
    order = place_order(user=buyer, lines=[OrderLine(plan, 1, "stored@example.test")])
    item_id = order.items.only("pk").get().pk
    operator = User.objects.create_superuser("operator@example.test")
    reveal_customer_input(order_id=order.pk, item_id=item_id, actor=operator, ip=None)
    with connection.cursor() as cursor:
        cursor.execute("SELECT customer_input FROM orders_orderitem WHERE id = %s", [item_id])
        ciphertext = cursor.fetchone()[0]
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([("orders", "0009_natural_panel_wording")])
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('orders_credentialaccesslog')")
            assert cursor.fetchone() == (None,)
            cursor.execute("SELECT customer_input FROM orders_orderitem WHERE id = %s", [item_id])
            assert cursor.fetchone() == (ciphertext,)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert not CredentialAccessLog.objects.exists()
    assert OrderItem.objects.get(pk=item_id).customer_input == "stored@example.test"


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
            executor = MigrationExecutor(connection)
            executor.migrate([previous])
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


def test_original_expiry_backfill_and_operator_audit_survive_rollback(buyer, plan):
    from django.db import IntegrityError, transaction

    previous = ("orders", "0023_freeze_warranty_deadlines")
    current = ("orders", "0024_claim_refunds")

    def move(target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    try:
        old = move(previous)
        order = old.get_model("orders", "Order").objects.create(
            user_id=buyer.pk, subtotal=400000, total_amount=400000
        )
        Item, History = old.get_model("orders", "OrderItem"), old.get_model(
            "orders", "OrderItemEvent"
        )
        now = timezone.now()
        original = timezone.localdate(now) + timedelta(days=30)
        later = original + timedelta(days=30)
        one, replaced = [
            Item.objects.create(
                order=order,
                plan_id=plan.pk,
                price_snapshot=200000,
                cost_snapshot=100000,
                status="DELIVERED",
                delivered_at=now,
                expires_at=expiry,
                warranty_ends_on=original,
                product_snapshot={"warranty": "full_period"},
            )
            for expiry in (original, later)
        ]
        History.objects.create(
            order_item_id=replaced.pk,
            from_status="REPLACEMENT_REQUESTED",
            to_status="DELIVERED",
            actor="operator",
        )
        Item.objects.filter(pk=replaced.pk).update(
            product_snapshot={"warranty": "days_7"},
            warranty_ends_on=timezone.localdate(now) + timedelta(days=7),
        )
        with pytest.raises(RuntimeError, match=f"Original subscription end.*{replaced.pk}"):
            move(current)
        Item.objects.filter(pk=replaced.pk).update(
            product_snapshot={"warranty": "full_period"}, warranty_ends_on=original
        )
        new = move(current)
        Item, History = new.get_model("orders", "OrderItem"), new.get_model(
            "orders", "OrderItemEvent"
        )
        assert list(Item.objects.order_by("pk").values_list("first_expires_on", flat=True)) == [
            original,
            original,
        ]
        fields = {
            "order_item_id": one.pk,
            "from_status": "DELIVERED",
            "to_status": "REPLACEMENT_REQUESTED",
            "actor": "operator",
            "claim_reason": "details_failed",
            "note": "Reported to support",
        }
        event_id = History.objects.create(**fields).pk
        old = move(previous)
        History = old.get_model("orders", "OrderItemEvent")
        assert History.objects.filter(pk=event_id, claim_reason="details_failed").exists()
        with pytest.raises(IntegrityError), transaction.atomic():
            History.objects.create(**fields)
        with pytest.raises(IntegrityError), transaction.atomic():
            History.objects.filter(pk=event_id).update(note="Edited history")
        new = move(current)
        assert (
            new.get_model("orders", "OrderItem").objects.get(pk=replaced.pk).first_expires_on
            == original
        )
        assert (
            new.get_model("orders", "OrderItemEvent").objects.get(pk=event_id).note
            == fields["note"]
        )
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_original_expiry_backfill_stops_for_an_item_replaced_before_the_warranty_freeze(
    buyer, plan
):
    from django.db.migrations.recorder import MigrationRecorder

    previous = ("orders", "0023_freeze_warranty_deadlines")
    freeze = MigrationRecorder.Migration.objects.filter(app="orders", name=previous[1])
    applied = freeze.get().applied
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        old = executor.loader.project_state([previous]).apps
        order = old.get_model("orders", "Order").objects.create(
            user_id=buyer.pk, subtotal=200000, total_amount=200000
        )
        replacement_end = timezone.localdate() + timedelta(days=60)
        # Retention had deleted the sets 0023 counted, so it froze the replacement's expiry.
        item = old.get_model("orders", "OrderItem").objects.create(
            order=order,
            plan_id=plan.pk,
            price_snapshot=200000,
            cost_snapshot=100000,
            status="DELIVERED",
            delivered_at=timezone.now(),
            expires_at=replacement_end,
            warranty_ends_on=replacement_end,
            product_snapshot={"warranty": "full_period"},
        )
        old.get_model("orders", "OrderItemEvent").objects.create(
            order_item_id=item.pk,
            from_status="REPLACEMENT_REQUESTED",
            to_status="DELIVERED",
            actor="operator",
        )
        freeze.update(applied=timezone.now() + timedelta(minutes=1))
        with pytest.raises(RuntimeError, match=f"Original subscription end.*{item.pk}"):
            MigrationExecutor(connection).migrate([("orders", "0024_claim_refunds")])
    finally:
        freeze.update(applied=applied)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

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


def test_stock_migrations_reverse_and_preserve_existing_data(plan):
    from apps.accounts.models import User
    from apps.cart.models import Cart, CartItem

    user = User.objects.create_user("stock-migration@example.test")
    cart = Cart.objects.create(user=user)
    item = CartItem.objects.create(cart=cart, plan=plan, quantity=1)
    try:
        MigrationExecutor(connection).migrate(
            [("cart", "0001_initial"), ("catalog", "0024_product_brand_colour")]
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT quantity FROM cart_cartitem WHERE id = %s", [item.pk])
            assert cursor.fetchone() == (1,)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        item.refresh_from_db()
        plan.refresh_from_db()
        assert item.quantity == 1 and item.reserved_until is None and plan.stock_quantity is None
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

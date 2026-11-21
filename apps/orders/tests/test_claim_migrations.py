from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.core.fields import EncryptedTextField
from apps.orders.models import OrderItem
from apps.orders.tests.test_customer_claim import delivered
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = pytest.mark.django_db(transaction=True)


def test_claim_migration_freezes_known_original_deadlines_and_reverses(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator, warranty="full_period", days=10)
    end = timezone.localdate() + timedelta(days=10)
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    before = [("orders", "0021_customer_delivery")]
    try:
        executor.migrate(before)
        with patch.object(
            EncryptedTextField,
            "from_db_value",
            side_effect=AssertionError("migration decrypted input"),
        ):
            MigrationExecutor(connection).migrate(leaves)
        assert OrderItem.objects.get(pk=item.pk).warranty_ends_on == end
        MigrationExecutor(connection).migrate(before)
        old = (
            MigrationExecutor(connection)
            .loader.project_state(before)
            .apps.get_model("orders", "OrderItem")
        )
        assert old.objects.get(pk=item.pk).expires_at == end
    finally:
        MigrationExecutor(connection).migrate(leaves)

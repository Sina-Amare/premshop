"""0023: «موضع درباره‌ی ایران» renamed to «نکات استفاده», its text kept both ways."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("catalog", "0022_account_email_labels")]
AFTER = [("catalog", "0023_usage_notes")]
WRITTEN = "پشتیبانی‌نشده"


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def stance_written():
    apps = migrate(BEFORE)
    category = apps.get_model("catalog", "Category").objects.create(name="هوش مصنوعی", slug="ai")
    apps.get_model("catalog", "Product").objects.create(
        category=category, name="ChatGPT Plus", slug="chatgpt-plus", iran_stance=WRITTEN
    )
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_the_stance_already_written_becomes_the_notes_and_comes_back(stance_written):
    Product = migrate(AFTER).get_model("catalog", "Product")
    assert Product.objects.get(slug="chatgpt-plus").usage_notes == WRITTEN

    Product = migrate(BEFORE).get_model("catalog", "Product")
    assert Product.objects.get(slug="chatgpt-plus").iran_stance == WRITTEN

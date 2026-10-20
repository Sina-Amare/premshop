"""0022: the plan labels seeded before the glossary's «اکانت», renamed in place."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("catalog", "0021_delivery_note_printed_as_written")]
AFTER = [("catalog", "0022_account_email_labels")]

LABELS = [
    "ایمیل حساب OpenAI",
    "ایمیل حساب Claude",
    "ایمیل حساب Spotify",
    "ایمیل حساب Spotify شما",
    "آدرس Gmail",
]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def old_labels():
    apps = migrate(BEFORE)
    category = apps.get_model("catalog", "Category").objects.create(name="هوش مصنوعی", slug="ai")
    product = apps.get_model("catalog", "Product").objects.create(
        category=category, name="ChatGPT Plus", slug="chatgpt-plus"
    )
    Plan = apps.get_model("catalog", "Plan")
    for n, label in enumerate(LABELS):
        Plan.objects.create(
            product=product, title=str(n), cost_price=1, sale_price=1, customer_input_label=label
        )
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def labels(apps) -> list[str]:
    return list(
        apps.get_model("catalog", "Plan")
        .objects.order_by("pk")
        .values_list("customer_input_label", flat=True)
    )


def test_only_the_seeded_labels_are_renamed_and_going_back_restores_them(old_labels):
    assert labels(migrate(AFTER)) == [
        "ایمیل اکانت OpenAI",
        "ایمیل اکانت Claude",
        "ایمیل اکانت Spotify",
        "ایمیل حساب Spotify شما",
        "آدرس Gmail",
    ]
    assert labels(migrate(BEFORE)) == LABELS

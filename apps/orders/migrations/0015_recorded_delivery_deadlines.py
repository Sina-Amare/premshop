from django.db import migrations, models
from django.db.models.functions import Cast, Length

from apps.orders.clock import delivery_deadline


def recorded_deadlines(apps, schema_editor):
    Item = apps.get_model("orders", "OrderItem")
    rows = (
        Item.objects.using(schema_editor.connection.alias)
        .defer("customer_input")
        .annotate(input_length=Length(Cast("customer_input", models.TextField())))
    )
    for item in rows.iterator():
        facts = item.product_snapshot
        kind = facts.get("input_type")
        required = facts.get("requires_customer_input")
        if (
            facts.get("telegram_required") is not False
            or kind not in ("none", "email", "username")
            or required is not (kind != "none")
        ):
            continue
        # Before receipt controls exist, non-empty input was stored at placement.
        received = item.created_at if required and item.input_length else None
        hours = facts.get("delivery_hours")
        due = None
        if type(hours) is int and 1 <= hours <= 48:
            due = delivery_deadline(
                paid_at=item.paid_at,
                delivery_hours=hours,
                requires_input=required,
                input_received_at=received,
            )
        Item.objects.using(schema_editor.connection.alias).filter(pk=item.pk).update(
            due_at=due, input_received_at=received
        )


def clear_deadlines(apps, schema_editor):
    apps.get_model("orders", "OrderItem").objects.using(schema_editor.connection.alias).update(
        due_at=None, input_received_at=None
    )


class Migration(migrations.Migration):
    dependencies = [("orders", "0014_delivery_clock")]
    operations = [migrations.RunPython(recorded_deadlines, clear_deadlines)]

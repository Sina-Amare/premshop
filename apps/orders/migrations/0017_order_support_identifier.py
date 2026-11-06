import secrets

from django.db import migrations, models
import django.db.models.deletion

import apps.core.fields
import apps.orders.models


def populate_support_identifiers(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Identifier = apps.get_model("orders", "OrderSupportIdentifier")
    alias = schema_editor.connection.alias
    for pk in Order.objects.using(alias).values_list("pk", flat=True).iterator():
        token = "".join(secrets.choice("23456789ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(12))
        Identifier.objects.using(alias).create(order_id=pk, support_token=token)


class Migration(migrations.Migration):
    dependencies = [("orders", "0016_input_waiting_clock")]
    operations = [
        migrations.CreateModel(
            name="OrderSupportIdentifier",
            fields=[
                (
                    "order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="support_identifier",
                        serialize=False,
                        to="orders.order",
                    ),
                ),
                (
                    "support_token",
                    apps.core.fields.EncryptedTextField(
                        default=apps.orders.models.support_token,
                        editable=False,
                    ),
                ),
            ],
        ),
        migrations.RunPython(populate_support_identifiers, migrations.RunPython.noop),
    ]

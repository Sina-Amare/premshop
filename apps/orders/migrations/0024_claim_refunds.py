from datetime import timedelta

from django.db import migrations, models
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

REASONS = ("details_failed", "subscription_mismatch", "stopped_early")
CONSTRAINT = "order_event_claim_reason_valid"
CLAIM_EVENT = (
    "claim_reason IN ('details_failed', 'subscription_mismatch', 'stopped_early')"
    " AND from_status = 'DELIVERED' AND to_status = 'REPLACEMENT_REQUESTED'"
)
CUSTOMER = "actor = 'customer' AND note = ''"
OPERATOR = "actor = 'operator' AND note <> ''"
DROP = f"ALTER TABLE orders_orderitemevent DROP CONSTRAINT {CONSTRAINT}"
ADD = f"ALTER TABLE orders_orderitemevent ADD CONSTRAINT {CONSTRAINT} CHECK"


def original_ends(apps, schema_editor):
    items = apps.get_model("orders", "OrderItem").objects.using(schema_editor.connection.alias)
    events = apps.get_model("orders", "OrderItemEvent").objects.using(
        schema_editor.connection.alias
    )
    replaced = events.filter(
        order_item_id=OuterRef("pk"),
        from_status="REPLACEMENT_REQUESTED",
        to_status="DELIVERED",
        note="",
    )
    rows = (
        items.filter(delivered_at__isnull=False)
        .only(
            "pk",
            "first_expires_on",
            "expires_at",
            "warranty_ends_on",
            "product_snapshot",
            "delivered_at",
        )
        .annotate(replaced=Exists(replaced))
    )
    unknown = []
    for item in rows.iterator():
        if not item.replaced:
            yield item, item.expires_at
            continue
        warranty_end = item.warranty_ends_on
        warranty = item.product_snapshot.get("warranty")
        if warranty_end is not None and (
            warranty == "full_period"
            or warranty == "days_7"
            and warranty_end < timezone.localdate(item.delivered_at) + timedelta(days=7)
        ):
            yield item, warranty_end
        else:
            unknown.append(item.pk)
    if unknown:
        raise RuntimeError(
            f"Original subscription end needs confirmation for order items {unknown}"
        )


def freeze(apps, schema_editor):
    Item = apps.get_model("orders", "OrderItem")
    items = Item.objects.using(schema_editor.connection.alias)
    for item, end in original_ends(apps, schema_editor):
        if end is not None:
            items.filter(pk=item.pk).update(first_expires_on=end)


def check_recoverable(apps, schema_editor):
    for item, end in original_ends(apps, schema_editor):
        if item.first_expires_on != end:
            raise RuntimeError(
                f"Reversing would lose the original subscription end for item {item.pk}"
            )


class Migration(migrations.Migration):
    dependencies = [("orders", "0023_freeze_warranty_deadlines")]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="first_expires_on",
            field=models.DateField("پایان اشتراک در تحویل اول", editable=False, null=True),
        ),
        migrations.RunPython(freeze, check_recoverable),
        migrations.RunSQL(
            sql=[
                DROP,
                f"{ADD} (claim_reason = '' OR (from_status IS NOT NULL AND {CLAIM_EVENT}"
                f" AND (({CUSTOMER}) OR ({OPERATOR}))))",
            ],
            # PostgreSQL checks new writes while leaving existing append-only history intact.
            reverse_sql=[
                DROP,
                f"{ADD} (claim_reason = '' OR ({CLAIM_EVENT} AND {CUSTOMER})) NOT VALID",
            ],
            state_operations=[
                migrations.RemoveConstraint(model_name="orderitemevent", name=CONSTRAINT),
                migrations.AddConstraint(
                    model_name="orderitemevent",
                    constraint=models.CheckConstraint(
                        name=CONSTRAINT,
                        condition=Q(claim_reason="")
                        | (
                            Q(claim_reason__in=REASONS)
                            & Q(
                                from_status__isnull=False,
                                from_status="DELIVERED",
                                to_status="REPLACEMENT_REQUESTED",
                            )
                            & (Q(actor="customer", note="") | (Q(actor="operator") & ~Q(note="")))
                        ),
                    ),
                ),
            ],
        ),
    ]

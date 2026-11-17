from datetime import timedelta

from django.db import migrations
from django.db.models import Count
from django.utils import timezone


def freeze_deadlines(apps, schema_editor):
    Item = apps.get_model("orders", "OrderItem")
    items = (
        Item.objects.filter(delivered_at__isnull=False)
        .only("pk", "product_snapshot", "delivered_at", "expires_at")
        .annotate(sets=Count("deliveries"))
    )
    for item in items.iterator():
        warranty = item.product_snapshot.get("warranty")
        if warranty not in ("days_7", "full_period"):
            continue
        if item.sets > 1:
            raise RuntimeError("Original warranty deadline needs confirmation for a replaced item")
        end = item.expires_at
        if warranty == "days_7":
            end = timezone.localdate(item.delivered_at) + timedelta(days=7)
            if item.expires_at:
                end = min(end, item.expires_at)
        Item.objects.filter(pk=item.pk).update(warranty_ends_on=end)


class Migration(migrations.Migration):
    dependencies = [("orders", "0022_orderitem_warranty_ends_on_and_more")]
    operations = [migrations.RunPython(freeze_deadlines, migrations.RunPython.noop)]

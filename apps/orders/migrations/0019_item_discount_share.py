from django.db import migrations, models


def fill_shares(apps, schema_editor):
    """Each existing order's discount over the items its code covered.

    The rule is copied here, not imported, so a later change to the service cannot change
    what this migration did.
    """
    Order = apps.get_model("orders", "Order")
    OrderItem = apps.get_model("orders", "OrderItem")
    for order in Order.objects.filter(discount_amount__gt=0).order_by("pk"):
        items = list(OrderItem.objects.filter(order=order).select_related("plan").order_by("pk"))
        snapshot = order.discount_snapshot or {}
        covered = [
            item
            for item in items
            if snapshot.get("scope") != "selected"
            or item.plan.product_id in (snapshot.get("products") or [])
        ]
        # A plan moved to another product since placement: spread over every item instead.
        if sum(item.price_snapshot for item in covered) < order.discount_amount:
            covered = items
        total = sum(item.price_snapshot for item in covered)
        shares = {item.pk: item.price_snapshot * order.discount_amount // total for item in covered}
        # A free item cannot take a leftover toman: its share would pass its price.
        payable = sorted(item.pk for item in covered if item.price_snapshot > 0)
        for pk in payable[: int(order.discount_amount - sum(shares.values()))]:
            shares[pk] += 1
        for pk, share in shares.items():
            OrderItem.objects.filter(pk=pk).update(discount_share=share)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0033_delivery"),
        ("orders", "0018_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="discount_share",
            field=models.DecimalField(
                decimal_places=0,
                default=0,
                editable=False,
                max_digits=12,
                verbose_name="سهم تخفیف",
            ),
        ),
        migrations.RunPython(fill_shares, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("discount_share__gte", 0), ("discount_share__lte", models.F("price_snapshot"))
                ),
                name="order_item_discount_share_within_price",
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0004_input_ladder"),
        ("orders", "0022_orderitem_warranty_ends_on_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="notification",
            name="notification_template_valid",
        ),
        migrations.AlterField(
            model_name="notification",
            name="template_name",
            field=models.CharField(
                choices=[
                    ("purchase_confirmation", "تأییدیه\u200cی خرید"),
                    ("payment_confirmed", "تأیید پرداخت"),
                    ("input_requested", "درخواست اطلاعات"),
                    ("input_reminder", "یادآوری اطلاعات"),
                    ("input_final_warning", "آخرین یادآوری اطلاعات"),
                    ("delivery_completed", "تحویل محصول"),
                    ("replacement_rejected", "رد درخواست تعویض"),
                    ("supply_delayed", "تغییر مهلت تحویل"),
                    ("item_cancelled", "لغو محصول"),
                    ("item_refunded", "واریز بازپرداخت"),
                    ("customer_claim", "درخواست تعویض مشتری"),
                ],
                default="purchase_confirmation",
                editable=False,
                max_length=32,
                verbose_name="نوع پیام",
            ),
        ),
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "template_name__in",
                        [
                            "purchase_confirmation",
                            "payment_confirmed",
                            "input_requested",
                            "input_reminder",
                            "input_final_warning",
                            "delivery_completed",
                            "replacement_rejected",
                            "supply_delayed",
                            "item_cancelled",
                            "item_refunded",
                            "customer_claim",
                        ],
                    )
                ),
                name="notification_template_valid",
            ),
        ),
    ]

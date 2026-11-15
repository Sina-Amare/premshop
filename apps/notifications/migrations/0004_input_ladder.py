from django.db import migrations, models

TEMPLATES = [
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
]


def drop_ladder_rows(apps, schema_editor):
    # The earlier check has no place for the ladder's two notices, so reversing drops them.
    apps.get_model("notifications", "Notification").objects.filter(
        template_name__in=["input_reminder", "input_final_warning"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0003_telegram_rows"),
        ("orders", "0019_item_discount_share"),
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
                    ("purchase_confirmation", "تأییدیه‌ی خرید"),
                    ("payment_confirmed", "تأیید پرداخت"),
                    ("input_requested", "درخواست اطلاعات"),
                    ("input_reminder", "یادآوری اطلاعات"),
                    ("input_final_warning", "آخرین یادآوری اطلاعات"),
                    ("delivery_completed", "تحویل محصول"),
                    ("replacement_rejected", "رد درخواست تعویض"),
                    ("supply_delayed", "تغییر مهلت تحویل"),
                    ("item_cancelled", "لغو محصول"),
                    ("item_refunded", "واریز بازپرداخت"),
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
                condition=models.Q(template_name__in=TEMPLATES),
                name="notification_template_valid",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, drop_ladder_rows),
    ]

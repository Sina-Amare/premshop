from django.db import migrations, models
from django.db.models import OuterRef, Subquery, Value
from django.db.models.functions import Coalesce


def record_the_accounts_number(apps, schema_editor):
    # An earlier order's number at purchase was never kept; the account's is the best record.
    User = apps.get_model("accounts", "User")
    apps.get_model("orders", "Order").objects.update(
        placement_phone=Coalesce(
            Subquery(User.objects.filter(pk=OuterRef("user_id")).values("phone")[:1]), Value("")
        )
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_voice_review_help"),
        ("orders", "0028_holiday"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="placement_phone",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                max_length=20,
                verbose_name="شماره‌ی موبایل زمان خرید",
            ),
        ),
        migrations.RunPython(record_the_accounts_number, migrations.RunPython.noop),
    ]

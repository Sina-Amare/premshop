from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0010_credential_access_log")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="policy_snapshot",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
    ]

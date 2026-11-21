from django.db import migrations


def drop_claim_rows(apps, schema_editor):
    # 0005's earlier check has no place for the claim alert, so reversing drops it first.
    apps.get_model("notifications", "Notification").objects.filter(
        template_name="customer_claim"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0005_remove_notification_notification_template_valid_and_more")
    ]
    operations = [migrations.RunPython(migrations.RunPython.noop, drop_claim_rows)]

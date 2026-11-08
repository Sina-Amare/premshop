from importlib import import_module

from django.db import migrations

drafts = import_module("apps.cms.migrations.0004_legal_page_drafts")
latin = import_module("apps.cms.migrations.0007_about_page_latin_names")

PREVIOUS = latin.DRAFTS
REFUNDS = {
    **PREVIOUS["refunds"],
    "body": PREVIOUS["refunds"]["body"].replace(
        "در همه‌ی این موارد، تمام مبلغی را که برای همان محصول پرداخت کرده‌اید برمی‌گردانیم.",
        "اگر محصول را تحویل نگرفته باشید، تمام مبلغی را که برای همان محصول پرداخت کرده‌اید"
        " برمی‌گردانیم. اگر محصول تحویل شده باشد و در مدت گارانتی نتوانیم آن را تعویض کنیم،"
        " مبلغ روزهای استفاده‌نشده را برمی‌گردانیم.",
    ),
}
DRAFTS = {**PREVIOUS, "refunds": REFUNDS}


def revise(apps, schema_editor):
    drafts._replace(apps, schema_editor, {"refunds": PREVIOUS["refunds"]}, {"refunds": REFUNDS})


def restore(apps, schema_editor):
    drafts._replace(apps, schema_editor, {"refunds": REFUNDS}, {"refunds": PREVIOUS["refunds"]})


class Migration(migrations.Migration):
    dependencies = [("cms", "0007_about_page_latin_names")]
    operations = [migrations.RunPython(revise, restore)]

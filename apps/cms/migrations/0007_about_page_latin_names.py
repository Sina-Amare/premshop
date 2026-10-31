from importlib import import_module

from django.db import migrations

drafts = import_module("apps.cms.migrations.0004_legal_page_drafts")
plain = import_module("apps.cms.migrations.0006_plain_legal_pages")

PREVIOUS = plain.DRAFTS
ABOUT = {
    **PREVIOUS["about"],
    "body": PREVIOUS["about"]["body"].replace(
        "گیفت کارت اپل و گوگل پلی", "گیفت کارت Apple و Google Play"
    ),
}
DRAFTS = {**PREVIOUS, "about": ABOUT}


def revise(apps, schema_editor):
    drafts._replace(apps, schema_editor, {"about": PREVIOUS["about"]}, {"about": ABOUT})


def restore(apps, schema_editor):
    drafts._replace(apps, schema_editor, {"about": ABOUT}, {"about": PREVIOUS["about"]})


class Migration(migrations.Migration):
    dependencies = [("cms", "0006_plain_legal_pages")]
    operations = [migrations.RunPython(revise, restore)]

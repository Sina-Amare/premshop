"""The glossary's «اکانت» in the plan labels seeded before it was applied.

seed_catalog writes «ایمیل اکانت …», but plans seeded earlier still say
«ایمیل حساب …», and the product page prints the label in the buy box and in
the answer about passwords. Only those exact labels change, so one the operator
has rewritten is left as it is.
"""

from django.db import migrations

RENAMED = {f"ایمیل حساب {name}": f"ایمیل اکانت {name}" for name in ("OpenAI", "Claude", "Spotify")}


def relabel(mapping):
    def run(apps, schema_editor):
        Plan = apps.get_model("catalog", "Plan")
        for old, new in mapping.items():
            Plan.objects.filter(customer_input_label=old).update(customer_input_label=new)

    return run


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0021_delivery_note_printed_as_written"),
    ]

    operations = [
        migrations.RunPython(relabel(RENAMED), relabel({new: old for old, new in RENAMED.items()})),
    ]

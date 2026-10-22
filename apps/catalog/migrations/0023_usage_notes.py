"""«موضع درباره‌ی ایران» becomes «نکات استفاده»: the operator's own notes on
using the product, in restricted Markdown, still required to publish.

A rename, so a stance already written stays as the product's notes. The old
column held 200 characters: reversing fails while any note is longer.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0022_account_email_labels"),
    ]

    operations = [
        migrations.RenameField(
            model_name="product", old_name="iran_stance", new_name="usage_notes"
        ),
        migrations.AlterField(
            model_name="product",
            name="usage_notes",
            field=models.TextField(
                blank=True,
                default="",
                help_text="هر نکته در یک سطر که با - شروع می\u200cشود. اگر نکته\u200cای ندارد، «موضوعیت ندارد».",
                verbose_name="نکات استفاده",
            ),
        ),
    ]

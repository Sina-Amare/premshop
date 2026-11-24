from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0033_delivery"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="product",
                    name="status",
                    field=models.CharField(
                        choices=[
                            ("draft", "پیش‌نویس"),
                            ("active", "فعال"),
                            ("unavailable", "ناموجود"),
                        ],
                        default="draft",
                        help_text="محصول را روی «فعال» بگذارید تا در فهرست فروشگاه نمایش داده شود.",
                        max_length=16,
                        verbose_name="وضعیت",
                    ),
                ),
            ],
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0020_descriptions_into_the_content_model"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="delivery_note",
            field=models.CharField(
                blank=True,
                default="",
                help_text="اگر پر کنید، مشتری همین متن را همان‌طور که نوشته‌اید به‌جای «تحویل تا … ساعت» می‌بیند.",
                max_length=120,
                verbose_name="متن دلخواه مهلت",
            ),
        ),
    ]

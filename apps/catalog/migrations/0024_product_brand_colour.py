from django.core.validators import RegexValidator
from django.db import migrations, models

# Reviewed from the existing marks; ChatGPT's green follows its approved card.
COLOURS = {
    "adobe-cc": "#fa0f00",
    "apple-gift-card": "#737373",
    "chatgpt-plus": "#10a37f",
    "claude-pro": "#d97757",
    "gemini-advanced": "#08b962",
    "github-copilot": "#737373",
    "google-play-gift-card": "#ea4335",
    "jetbrains-all": "#fe2857",
    "midjourney": "#1a222d",
    "netflix": "#e50914",
    "spotify-premium": "#1ed760",
    "youtube-premium": "#ff0000",
}


def fill_colours(apps, schema_editor):
    products = apps.get_model("catalog", "Product").objects.using(schema_editor.connection.alias)
    for product in products.only("pk", "slug", "brand_colour").iterator():
        if not product.brand_colour:
            products.filter(pk=product.pk).update(brand_colour=COLOURS.get(product.slug, "#737373"))


class Migration(migrations.Migration):
    dependencies = [("catalog", "0023_usage_notes")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="brand_colour",
            field=models.CharField(
                blank=True,
                default="",
                help_text="رنگ زمینه‌ی کارت، مانند #10a37f. خالی یعنی زمینه‌ی خنثی.",
                max_length=7,
                validators=[
                    RegexValidator(r"\A#[0-9a-fA-F]{6}\Z", "رنگ را مانند #10a37f وارد کنید.")
                ],
                verbose_name="رنگ برند",
            ),
        ),
        migrations.RunPython(fill_colours, migrations.RunPython.noop),
    ]

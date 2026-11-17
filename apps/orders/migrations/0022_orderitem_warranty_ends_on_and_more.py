from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0021_customer_delivery"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="warranty_ends_on",
            field=models.DateField(
                editable=False, null=True, verbose_name="پایان گارانتی زمان تحویل"
            ),
        ),
        migrations.AddField(
            model_name="orderitemevent",
            name="claim_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    (
                        "details_failed",
                        "اطلاعات ورود اکانت یا کد تحویل\u200cداده\u200cشده درست نیست.",
                    ),
                    ("subscription_mismatch", "اشتراک فعال نیست یا نوع آن با سفارش فرق دارد."),
                    (
                        "stopped_early",
                        "اشتراک کار می\u200cکرد، اما پیش از پایان گارانتی قطع شده است.",
                    ),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitemevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("claim_reason", ""),
                    models.Q(
                        (
                            "claim_reason__in",
                            ["details_failed", "subscription_mismatch", "stopped_early"],
                        ),
                        ("actor", "customer"),
                        ("from_status", "DELIVERED"),
                        ("note", ""),
                        ("to_status", "REPLACEMENT_REQUESTED"),
                    ),
                    _connector="OR",
                ),
                name="order_event_claim_reason_valid",
            ),
        ),
    ]

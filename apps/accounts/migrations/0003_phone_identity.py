"""The mobile number becomes the account's identity; email becomes an optional address.

An account with no number keeps NULL, which the unique index allows any number of.
Going back makes email the username again, so it is refused while an account has
no email or two accounts share one.
"""

from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower


def blank_phone_to_null(apps, schema_editor):
    apps.get_model("accounts", "User").objects.filter(phone="").update(phone=None)


def null_phone_to_blank(apps, schema_editor):
    apps.get_model("accounts", "User").objects.filter(phone__isnull=True).update(phone="")


def refuse_without_one_email_each(apps, schema_editor):
    users = apps.get_model("accounts", "User").objects
    if users.filter(email="").exists():
        raise RuntimeError("An account has no email, so email cannot be the username again.")
    shared = (
        users.annotate(address=Lower("email"))
        .values("address")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    if shared.exists():
        raise RuntimeError("Two accounts share an email, so email cannot be the username again.")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_labels_from_the_glossary")]

    operations = [
        migrations.RemoveConstraint(model_name="user", name="user_email_case_insensitive_unique"),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(blank=True, default="", max_length=254, verbose_name="ایمیل"),
        ),
        migrations.RunPython(migrations.RunPython.noop, refuse_without_one_email_each),
        migrations.AlterField(
            model_name="user",
            name="phone",
            field=models.CharField(
                blank=True, max_length=20, null=True, verbose_name="شماره‌ی موبایل"
            ),
        ),
        migrations.RunPython(blank_phone_to_null, null_phone_to_blank),
        migrations.AlterField(
            model_name="user",
            name="phone",
            field=models.CharField(
                blank=True, max_length=20, null=True, unique=True, verbose_name="شماره‌ی موبایل"
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("phone__isnull", True), ("phone__regex", "^09[0-9]{9}$"), _connector="OR"
                ),
                name="user_phone_normal_form",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="is_verified",
            field=models.BooleanField(default=False, verbose_name="شماره‌ی موبایل تأیید شده"),
        ),
    ]

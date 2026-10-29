from django.db import migrations


def create_drafts(apps, schema_editor):
    Page = apps.get_model("cms", "Page")
    for slug, title in [
        ("terms", "قوانین خرید"),
        ("refunds", "لغو سفارش و بازگشت وجه"),
        ("privacy", "حریم خصوصی"),
        ("about", "درباره ما"),
        ("contact", "تماس با ما"),
    ]:
        body = ""
        if slug == "refunds":
            body = (
                "[تصمیم مالک: انتخاب متن آغاز صفحه و تکمیل شرایط بازگشت وجه]\n\n"
                "## پیشنهاد اول\n\n"
                "اگر پیش از شروع فعال‌سازی یا تحویل محصول از خرید منصرف شوید، "
                "مبلغ پرداختی همان محصول را کامل برمی‌گردانیم. "
                "درخواست بازگشت وجه را همراه با شماره‌ی سفارش به پشتیبانی بفرستید.\n\n"
                "## پیشنهاد دوم\n\n"
                "اگر از خرید منصرف شده‌اید، پیش از شروع فعال‌سازی یا تحویل سفارش به ما اطلاع دهید. "
                "مبلغی را که برای آن محصول پرداخت کرده‌اید کامل برمی‌گردانیم."
            )
        Page.objects.using(schema_editor.connection.alias).get_or_create(
            slug=slug,
            defaults={"title": title, "body": body, "description": "", "is_published": False},
        )


class Migration(migrations.Migration):
    dependencies = [("cms", "0001_initial")]
    operations = [migrations.RunPython(create_drafts, migrations.RunPython.noop)]

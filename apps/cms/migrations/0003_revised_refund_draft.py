from django.db import migrations

# Both openings seeded in 0002 were rejected: supply starts after payment, so
# the draft offers a refund only when an order cannot be supplied in time.
REJECTED = (
    "[تصمیم مالک: انتخاب متن آغاز صفحه و تکمیل شرایط بازگشت وجه]\n\n"
    "## پیشنهاد اول\n\n"
    "اگر پیش از شروع فعال‌سازی یا تحویل محصول از خرید منصرف شوید، "
    "مبلغ پرداختی همان محصول را کامل برمی‌گردانیم. "
    "درخواست بازگشت وجه را همراه با شماره‌ی سفارش به پشتیبانی بفرستید.\n\n"
    "## پیشنهاد دوم\n\n"
    "اگر از خرید منصرف شده‌اید، پیش از شروع فعال‌سازی یا تحویل سفارش به ما اطلاع دهید. "
    "مبلغی را که برای آن محصول پرداخت کرده‌اید کامل برمی‌گردانیم."
)
REVISED = (
    "[تصمیم مالک: شرایط انصراف از خرید پس از پرداخت هنوز نهایی نشده است.]\n\n"
    "## تأمین نشدن سفارش\n\n"
    "اگر تا ۴۸ ساعت پس از دریافت اطلاعات لازم نتوانیم اکانتی را که سفارش داده‌اید تأمین کنیم، "
    "مبلغی را که برای آن پرداخته‌اید کامل برمی‌گردانیم.\n\n"
    "برای محصولی که به اطلاعات شما نیاز ندارد، این مهلت از زمان تأیید پرداخت آغاز می‌شود.\n\n"
    "[تصمیم مالک: شرایط گارانتی هر محصول، مهلت ارسال اطلاعات و قواعد انصراف از خرید تکمیل شود.]"
)


def _replace(apps, schema_editor, old, new):
    pages = apps.get_model("cms", "Page").objects.using(schema_editor.connection.alias)
    # Only the untouched seed is replaced; an edited page is the owner's text.
    pages.filter(slug="refunds", body=old).update(body=new)


def revise(apps, schema_editor):
    _replace(apps, schema_editor, REJECTED, REVISED)


def restore(apps, schema_editor):
    _replace(apps, schema_editor, REVISED, REJECTED)


class Migration(migrations.Migration):
    dependencies = [("cms", "0002_initial_drafts")]
    operations = [migrations.RunPython(revise, restore)]

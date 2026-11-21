from importlib import import_module

from django.db import migrations
from django.utils import timezone

PREVIOUS = import_module("apps.cms.migrations.0008_refund_amounts").DRAFTS
OLD = (
    "- محصول گارانتی داشته باشد و نتوانیم آن را تعویض کنیم.",
    "اگر محصول را تحویل نگرفته باشید، تمام مبلغی را که برای همان محصول پرداخت کرده‌اید"
    " برمی‌گردانیم. اگر محصول تحویل شده باشد و در مدت گارانتی نتوانیم آن را تعویض کنیم،"
    " مبلغ روزهای استفاده‌نشده را برمی‌گردانیم.",
)
NEW = (
    "- نتوانیم محصولی را که از ابتدا مشکل داشته یا در مدت گارانتی از کار افتاده است، تعویض کنیم.",
    "اگر محصول را تحویل نگرفته باشید، تمام مبلغی را که برای همان محصول پرداخت کرده‌اید"
    " برمی‌گردانیم. اگر محصول از تحویل اول درست کار نکرده باشد و نتوانیم با تعویض مشکل را حل کنیم،"
    " باز هم تمام مبلغ را برمی‌گردانیم."
    " مهلت گزارش این مشکل ۴۸ ساعت پس از هر تحویل یا تعویض است."
    " اگر محصول در مدت گارانتی از کار افتاده باشد و نتوانیم آن را"
    " تعویض کنیم، مبلغ روزهای استفاده‌نشده را از روز ثبت درخواست تا پایان اشتراک اولیه"
    " برمی‌گردانیم. در این حالت، اگر محصول در تحویل اول تاریخ پایان نداشته باشد،"
    " تمام مبلغ را برمی‌گردانیم.",
)


def replace_section(body, before, after):
    if all(body.count(line) == 1 for line in before):
        for old, new in zip(before, after, strict=True):
            body = body.replace(old, new, 1)
    return body


def update_page(apps, schema_editor, before, after):
    pages = apps.get_model("cms", "Page").objects.using(schema_editor.connection.alias)
    page = pages.filter(slug="refunds").values_list("pk", "body").first()
    if page:
        pk, body = page
        revised = replace_section(body, before, after)
        if revised != body:
            pages.filter(pk=pk, body=body).update(body=revised, updated_at=timezone.now())


def revise(apps, schema_editor):
    update_page(apps, schema_editor, OLD, NEW)


def restore(apps, schema_editor):
    update_page(apps, schema_editor, NEW, OLD)


DRAFTS = {
    **PREVIOUS,
    "refunds": {
        **PREVIOUS["refunds"],
        "body": replace_section(PREVIOUS["refunds"]["body"], OLD, NEW),
    },
}


class Migration(migrations.Migration):
    dependencies = [("cms", "0008_refund_amounts")]
    operations = [migrations.RunPython(revise, restore)]

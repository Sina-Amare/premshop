from importlib import import_module

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection

from apps.cms.models import Page

pytestmark = pytest.mark.django_db


def test_initial_content_is_private_and_the_refund_draft_cannot_be_published():
    pages = list(Page.objects.all())
    assert {p.slug for p in pages} == set(Page.Slug.values)
    assert not any(p.is_published for p in pages)
    refund = Page.objects.get(slug="refunds")
    assert "## تأمین نشدن سفارش" in refund.body
    assert "پیشنهاد" not in refund.body, "both rejected openings are gone"
    assert "[تصمیم مالک:" in refund.body


def test_revising_the_refund_draft_reverses_and_preserves_edits():
    migration = import_module("apps.cms.migrations.0003_revised_refund_draft")
    page = Page.objects.get(slug="refunds")
    page.body = migration.REVISED
    page.save()
    with connection.schema_editor(atomic=False) as editor:
        migration.restore(apps, editor)
    page.refresh_from_db()
    assert page.body == migration.REJECTED

    page.body = "متنی که صاحب فروشگاه ویرایش کرده است."
    page.save()
    with connection.schema_editor(atomic=False) as editor:
        migration.revise(apps, editor)
    page.refresh_from_db()
    assert page.body == "متنی که صاحب فروشگاه ویرایش کرده است."


def review_migration():
    return import_module("apps.cms.migrations.0004_legal_page_drafts")


def run_migration(function):
    with connection.schema_editor(atomic=False) as editor:
        function(apps, editor)


def content():
    return {
        page.slug: {
            "title": page.title,
            "body": page.body,
            "description": page.description,
        }
        for page in Page.objects.all()
    }


def test_all_five_review_drafts_have_descriptions_and_match_the_migration():
    migration = review_migration()
    assert content() == migration.DRAFTS
    assert len(migration.DRAFTS) == 5
    for page in Page.objects.all():
        assert not page.is_published
        assert page.body.strip()
        assert 0 < len(page.description) <= 300
        page.is_published = True
        with pytest.raises(ValidationError, match="پیش از انتشار"):
            page.full_clean()


def test_review_drafts_can_be_reversed_and_reapplied_without_duplicate_or_repeated_writes():
    migration = review_migration()
    run_migration(migration.restore)
    assert content() == migration.PREVIOUS
    run_migration(migration.revise)
    assert content() == migration.DRAFTS
    timestamps = list(Page.objects.values_list("pk", "updated_at"))
    run_migration(migration.revise)
    assert content() == migration.DRAFTS
    assert list(Page.objects.values_list("pk", "updated_at")) == timestamps


@pytest.mark.parametrize("direction", ["revise", "restore"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "عنوانی که صاحب فروشگاه انتخاب کرده است"),
        ("body", "متنی که صاحب فروشگاه ویرایش کرده است."),
        ("description", "توضیحی که صاحب فروشگاه نوشته است."),
    ],
)
def test_review_migration_preserves_every_edited_field_and_the_rest_of_its_page(
    direction, field, value
):
    migration = review_migration()
    if direction == "revise":
        run_migration(migration.restore)
    for page in Page.objects.all():
        setattr(page, field, value)
        page.save()
    before = content()
    timestamps = list(Page.objects.values_list("pk", "updated_at"))
    run_migration(getattr(migration, direction))
    assert content() == before
    assert list(Page.objects.values_list("pk", "updated_at")) == timestamps


def test_rollback_preserves_a_published_page_even_if_its_content_matches_the_seed():
    migration = review_migration()
    Page.objects.filter(slug="refunds").update(is_published=True)
    run_migration(migration.restore)
    page = Page.objects.get(slug="refunds")
    assert page.is_published
    assert page.body == migration.DRAFTS["refunds"]["body"]


def test_review_migration_does_not_recreate_a_deleted_page():
    migration = review_migration()
    Page.objects.filter(slug="about").delete()
    run_migration(migration.revise)
    assert not Page.objects.filter(slug="about").exists()


def test_terms_and_refunds_share_the_later_of_payment_and_required_details_clock():
    migration = review_migration()
    for slug in ("terms", "refunds"):
        assert migration.DELIVERY_CLOCK in migration.DRAFTS[slug]["body"]
    assert "هرکدام دیرتر باشد" in migration.DELIVERY_CLOCK
    assert "تأیید پرداخت" in migration.DELIVERY_CLOCK
    assert "دریافت اطلاعات" in migration.DELIVERY_CLOCK
    assert "۴۸ ساعت" in migration.DELIVERY_CLOCK
    assert "۲۱ روز تقویمی" in migration.DRAFTS["refunds"]["body"]
    assert "یک روز کاری" in migration.DRAFTS["refunds"]["body"]


def test_privacy_draft_names_data_services_without_claiming_active_error_reporting():
    body = Page.objects.get(slug="privacy").body
    for name in ("SMTP2GO", "Sentry", "GlitchTip", "Google Fonts"):
        assert name in body
    assert "گزارش خودکار خطا در حال حاضر فعال نیست" in body
    assert "از ابزار تحلیل رفتار بازدیدکنندگان استفاده نمی‌کنیم" in body


def test_reapplying_initial_content_preserves_edits():
    page = Page.objects.get(slug="refunds")
    page.body = "متنی که صاحب فروشگاه ویرایش کرده است."
    page.save()
    seed = import_module("apps.cms.migrations.0002_initial_drafts").create_drafts
    with connection.schema_editor(atomic=False) as editor:
        seed(apps, editor)
    page.refresh_from_db()
    assert page.body == "متنی که صاحب فروشگاه ویرایش کرده است."

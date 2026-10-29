from importlib import import_module

import pytest
from django.apps import apps
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


def test_reapplying_initial_content_preserves_edits():
    page = Page.objects.get(slug="refunds")
    page.body = "متنی که صاحب فروشگاه ویرایش کرده است."
    page.save()
    seed = import_module("apps.cms.migrations.0002_initial_drafts").create_drafts
    with connection.schema_editor(atomic=False) as editor:
        seed(apps, editor)
    page.refresh_from_db()
    assert page.body == "متنی که صاحب فروشگاه ویرایش کرده است."

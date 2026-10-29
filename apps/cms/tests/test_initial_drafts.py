from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection

from apps.cms.models import Page

pytestmark = pytest.mark.django_db


def test_initial_content_is_private_and_refund_alternatives_cannot_be_published():
    pages = list(Page.objects.all())
    assert {p.slug for p in pages} == set(Page.Slug.values)
    assert not any(p.is_published for p in pages)
    refund = Page.objects.get(slug="refunds")
    assert "پیشنهاد اول" in refund.body
    assert "پیشنهاد دوم" in refund.body
    assert "[تصمیم مالک:" in refund.body


def test_reapplying_initial_content_preserves_edits():
    page = Page.objects.get(slug="refunds")
    page.body = "متنی که صاحب فروشگاه ویرایش کرده است."
    page.save()
    seed = import_module("apps.cms.migrations.0002_initial_drafts").create_drafts
    with connection.schema_editor(atomic=False) as editor:
        seed(apps, editor)
    page.refresh_from_db()
    assert page.body == "متنی که صاحب فروشگاه ویرایش کرده است."

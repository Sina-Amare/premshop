from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.cms.models import Page

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("published", [False, True])
def test_legal_navigation_adds_one_query_with_zero_or_five_public_pages(client, published):
    for slug, title in Page.Slug.choices:
        Page.objects.update_or_create(
            slug=slug,
            defaults={
                "title": title,
                "body": "متن نهایی صفحه.",
                "description": "توضیح صفحه.",
                "is_published": published,
            },
        )
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("home"))
    assert response.status_code == 200
    assert len([q for q in queries if '"cms_page"' in q["sql"]]) == 1
    for slug in Page.Slug.values:
        assert (f'href="/{slug}/"' in response.content.decode()) is published


@pytest.fixture
def page():
    obj, _ = Page.objects.update_or_create(
        slug="refunds",
        defaults={
            "title": "لغو سفارش و بازگشت وجه",
            "body": "## پیش از فعال‌سازی\n\nمبلغ پرداختی را کامل برمی‌گردانیم.",
            "description": "شرایط لغو سفارش و بازگشت وجه.",
            "is_published": False,
        },
    )
    return obj


def staff(*permissions):
    user = User.objects.create_user("editor@example.test", is_staff=True, is_verified=True)
    user.user_permissions.add(
        *Permission.objects.filter(content_type__app_label="cms", codename__in=permissions)
    )
    return user


@pytest.mark.parametrize("role", ["anonymous", "customer", "staff", "customer-with-permission"])
@pytest.mark.parametrize("query", ["", "?preview=1"])
def test_a_draft_is_private_even_with_a_preview_parameter(client, page, role, query):
    if role != "anonymous":
        user = staff("view_page") if role == "customer-with-permission" else staff()
        user.is_staff = role == "staff"
        user.save()
        client.force_login(user)

    response = client.get(page.get_absolute_url() + query)

    assert response.status_code == 404
    assert "مبلغ پرداختی را کامل برمی‌گردانیم" not in response.content.decode()


def test_only_authorized_staff_can_preview_at_the_public_address(client, settings, page):
    settings.SITE_URL = "https://premshop.ir"
    client.force_login(staff("view_page"))

    response = client.get(page.get_absolute_url())
    body = response.content.decode()

    assert response.status_code == 200
    assert "پیش‌نویس" in body
    assert "مبلغ پرداختی را کامل برمی‌گردانیم" in body
    assert '<meta name="robots" content="noindex">' in body
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert {"private", "no-store"} <= set(response["Cache-Control"].split(", "))
    assert "Cookie" in response["Vary"]
    assert 'rel="canonical"' not in body
    assert 'property="og:url"' not in body
    assert "مبلغ پرداختی را کامل برمی‌گردانیم" not in client.get(reverse("home")).content.decode()


def test_staff_preview_cannot_be_reused_by_anonymous_visitors(client, page):
    client.force_login(staff("view_page"))
    assert client.get(page.get_absolute_url()).status_code == 200
    client.logout()
    assert client.get(page.get_absolute_url()).status_code == 404


def test_a_published_page_has_metadata_and_a_footer_link(client, settings, page):
    settings.SITE_URL = "https://premshop.ir"
    page.is_published = True
    page.save()

    response = client.get(page.get_absolute_url() + "?utm_source=telegram")
    body = response.content.decode()

    assert response.status_code == 200
    assert '<link rel="canonical" href="https://premshop.ir/refunds/">' in body
    assert f'<meta name="description" content="{page.description}">' in body
    assert "پیش‌نویس" not in body
    assert 'href="/refunds/"' in client.get(reverse("home")).content.decode()
    page.is_published = False
    page.save()
    assert client.get(page.get_absolute_url()).status_code == 404
    assert 'href="/refunds/"' not in client.get(reverse("home")).content.decode()


def test_a_draft_never_has_a_footer_link_even_for_staff(client, page):
    client.force_login(staff("view_page"))
    assert 'href="/refunds/"' not in client.get(reverse("home")).content.decode()


def test_operator_markdown_cannot_run_scripts(client, page):
    page.body = "<script>alert(1)</script>\n\n[کلیک](javascript:alert(1))\n\n## شرایط\n\nاشتراک ChatGPT Plus"
    page.save()
    client.force_login(staff("view_page"))

    body = client.get(page.get_absolute_url()).content.decode()

    assert "<script>alert(1)</script>" not in body
    assert 'href="javascript:' not in body
    assert "<h2>شرایط</h2>" in body
    assert "<bdi" in body


@pytest.mark.parametrize("field", ["title", "body", "description"])
@pytest.mark.parametrize("value", ["", "   ", "[تصمیم مالک: متن نهایی]", "[وابسته به تصمیم مالک]"])
def test_incomplete_content_cannot_be_published_even_outside_the_admin(page, field, value):
    setattr(page, field, value)
    page.is_published = True
    with pytest.raises(ValidationError):
        page.save()
    page.refresh_from_db()
    assert not page.is_published


def test_a_page_address_does_not_change_when_edited(page):
    page.slug = "terms"
    with pytest.raises(ValidationError, match="آدرس"):
        page.save()


def test_an_editor_cannot_publish_with_a_forged_form(client, page):
    client.force_login(staff("view_page", "change_page"))
    url = reverse("admin:cms_page_change", args=[page.pk])

    response = client.post(
        url,
        {
            "title": page.title,
            "body": "متن ویرایش‌شده",
            "description": page.description,
            "is_published": "on",
            "_save": "",
        },
    )

    assert response.status_code == 302
    page.refresh_from_db()
    assert page.body == "متن ویرایش‌شده"
    assert not page.is_published


def test_only_a_publisher_can_edit_an_already_published_page(client, page):
    page.is_published = True
    page.save()
    client.force_login(staff("view_page", "change_page"))

    response = client.post(reverse("admin:cms_page_change", args=[page.pk]), {"body": "جایگزین"})

    assert response.status_code == 403
    page.refresh_from_db()
    assert page.body != "جایگزین"


def test_authorized_publisher_can_publish_complete_content(client, page):
    client.force_login(staff("view_page", "change_page", "publish_page"))
    response = client.post(
        reverse("admin:cms_page_change", args=[page.pk]),
        {
            "title": page.title,
            "body": page.body,
            "description": page.description,
            "is_published": "on",
            "_save": "",
        },
    )
    assert response.status_code == 302
    page.refresh_from_db()
    assert page.is_published


def test_the_admin_opens_the_saved_page_for_preview(client, page):
    client.force_login(staff("view_page", "change_page"))
    body = client.get(reverse("admin:cms_page_change", args=[page.pk])).content.decode()
    assert page.get_absolute_url() in body
    assert 'name="slug"' not in body


def test_routes_are_limited_to_the_five_pages(client):
    assert [reverse(f"cms:{slug}") for slug in Page.Slug.values] == [
        "/terms/",
        "/refunds/",
        "/privacy/",
        "/about/",
        "/contact/",
    ]
    assert client.get("/not-a-cms-page/").status_code == 404

"""Public pages are discoverable without becoming available to buy."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from xml.etree import ElementTree

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product, ProductSection
from apps.cms.models import Page

pytestmark = pytest.mark.django_db
SITE = "https://premshop.ir"
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@pytest.fixture
def product():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        short_description="اشتراک Claude Pro روی اکانت خودتان.",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        usage_notes="موضوعیت ندارد",
        status=Product.Status.UNAVAILABLE,
    )
    Plan.objects.create(product=product, title="۱ ماهه", cost_price=1, sale_price=900000)
    assert product.publish_problems() == []
    return product


def sitemap(client, settings):
    settings.SITE_URL = SITE
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/xml")
    tree = ElementTree.fromstring(response.content)  # noqa: S314 -- Trusted test response.
    assert tree.tag == NS + "urlset"
    assert all([child.tag for child in url] == [NS + "loc"] for url in tree)
    return [url.findtext(NS + "loc") for url in tree]


def test_sitemap_excludes_drafts_and_private(client, settings, product):
    Page.objects.all().delete()
    about = Page.objects.create(
        slug="about", title="درباره ما", description="معرفی فروشگاه", body="متن", is_published=True
    )
    Page.objects.create(slug="terms", title="قوانین خرید")
    draft = Product.objects.create(category=product.category, name="Draft", slug="draft")
    urls = sitemap(client, settings)
    assert set(urls) == {
        SITE + "/",
        SITE + "/faq/",
        SITE + reverse("category", args=[product.category.slug]),
        SITE + product.get_absolute_url(),
        SITE + about.get_absolute_url(),
    }
    assert len(urls) == len(set(urls))
    for url in urls:
        response = client.get(url.removeprefix(SITE))
        assert response.status_code == 200, url
        assert 'content="noindex"' not in response.content.decode(), url
    assert SITE + draft.get_absolute_url() not in urls


def test_sitemap_is_independent_of_staff_preview_and_request_host(admin_client, settings, product):
    Product.objects.create(category=product.category, name="Draft", slug="draft")
    settings.ALLOWED_HOSTS = ["*"]
    urls = sitemap(admin_client, settings)
    response = admin_client.get("/sitemap.xml", HTTP_HOST="attacker.example")
    assert response.status_code == 200
    assert "attacker.example" not in response.content.decode()
    assert SITE + "/p/draft/" not in urls
    assert not any("?" in url for url in urls)


def test_sitemap_escapes_legacy_category_addresses(client, settings, product):
    Category.objects.filter(pk=product.category_id).update(slug="old&category")
    urls = sitemap(client, settings)
    assert SITE + "/c/old&category/" in urls
    assert b"old&amp;category" in client.get("/sitemap.xml").content


@pytest.mark.parametrize("problem", ["summary", "notes", "plan", "section", "stale"])
def test_incomplete_unavailable_product_has_no_public_page_or_sitemap_entry(
    client, admin_client, settings, product, problem
):
    if problem == "summary":
        product.short_description = ""
    elif problem == "notes":
        product.usage_notes = ""
    elif problem == "plan":
        product.plans.update(is_available=False)
    elif problem == "section":
        ProductSection.objects.create(product=product, kind="benefits", body="[تصمیم مالک: متن]")
    else:
        product.time_sensitive = True
        product.facts_checked_on = timezone.localdate() - timedelta(days=8)
    product.save()
    assert product.publish_problems()
    for visitor in (client, admin_client):
        assert visitor.get(product.get_absolute_url()).status_code == 404
        assert SITE + product.get_absolute_url() not in sitemap(visitor, settings)


def test_adobe_like_unavailable_product_explains_its_missing_content_in_panel(
    admin_client, product
):
    product.slug = "adobe-cc"
    product.name = "Adobe Creative Cloud"
    product.usage_notes = ""
    product.save()
    response = admin_client.get(reverse("admin:catalog_product_change", args=[product.pk]))
    assert response.status_code == 200
    assert response.context["readiness"] == product.publish_problems()
    body = response.content.decode()
    assert "صفحه‌ی این محصول نمایش داده نمی‌شود" in body
    for reason in product.publish_problems():
        assert reason in body


def test_public_page_selector_does_not_change_for_sale_selectors(product):
    assert not product.is_public
    assert not Product.objects.public().filter(pk=product.pk).exists()
    assert [p.pk for p in Product.objects.filter(pk=product.pk).public_pages()] == [product.pk]
    product.status = Product.Status.ACTIVE
    product.save()
    assert product.is_public
    assert Product.objects.public().filter(pk=product.pk).exists()


def test_unavailable_page_keeps_breadcrumbs_canonical_image_and_normal_caching(
    client, settings, product
):
    settings.SITE_URL = SITE
    response = client.get(product.get_absolute_url() + "?utm_source=telegram")
    assert response.status_code == 200
    body = response.content.decode()
    assert response.context["preview"] is False
    assert 'class="preview-bar"' not in body
    assert 'content="noindex"' not in body
    assert "no-store" not in response.get("Cache-Control", "")
    assert "private" not in response.get("Cache-Control", "")
    assert f'<link rel="canonical" href="{SITE}{product.get_absolute_url()}">' in body
    assert f'content="{SITE}/static/img/share/claude-pro.png"' in body
    schemas = [
        json.loads(value)
        for value in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    ]
    assert any(schema.get("@type") == "BreadcrumbList" for schema in schemas)
    assert (
        response.context["structured_data"]["itemListElement"][-1]["item"]
        == SITE + product.get_absolute_url()
    )


def test_unavailable_product_stays_out_of_sale_listings(client, product):
    for path in ("/", reverse("category", args=[product.category.slug]), "/search/?q=Claude"):
        body = client.get(path).content.decode()
        assert f'href="{product.get_absolute_url()}"' not in body


def test_staff_sees_an_unavailable_public_page_without_draft_state(admin_client, product):
    response = admin_client.get(product.get_absolute_url())
    assert response.status_code == 200
    assert response.context["preview"] is False
    assert response.context["structured_data"]["@type"] == "BreadcrumbList"
    assert "no-store" not in response.get("Cache-Control", "")
    assert 'content="noindex"' not in response.content.decode()


@pytest.mark.parametrize("status", [Product.Status.ACTIVE, Product.Status.UNAVAILABLE])
def test_non_draft_slug_is_readonly_and_forged_posts_cannot_change_it(
    admin_client, product, status
):
    from apps.catalog.tests.test_admin import form_of

    product.status = status
    product.save()
    path = reverse("admin:catalog_product_change", args=[product.pk])
    response = admin_client.get(path)
    assert 'name="slug"' not in response.content.decode()
    data = form_of(admin_client, path)
    data["slug"] = ["changed"]
    data["status"] = [Product.Status.DRAFT]
    response = admin_client.post(path, data)
    assert response.status_code == 302
    product.refresh_from_db()
    assert product.slug == "claude-pro"
    assert product.status == Product.Status.DRAFT
    assert 'name="slug"' in admin_client.get(path).content.decode()
    data = form_of(admin_client, path)
    data["slug"] = ["changed"]
    response = admin_client.post(path, data)
    assert response.status_code == 302
    product.refresh_from_db()
    assert product.slug == "changed"


def test_robots_allows_each_named_crawler_and_wildcard_in_separate_groups(client, settings):
    settings.SITE_URL = SITE
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    text = response.content.decode()
    groups = re.findall(r"User-agent: ([^\n]+)\nAllow: /\n", text)
    assert {
        "*",
        "Googlebot",
        "Bingbot",
        "GPTBot",
        "OAI-SearchBot",
        "ChatGPT-User",
        "ClaudeBot",
        "Google-Extended",
        "CCBot",
        "PerplexityBot",
    } <= set(groups)
    assert len(groups) == len(set(groups))
    assert text.count("User-agent:") == len(groups)
    assert "Disallow:" not in text
    assert f"Sitemap: {SITE}/sitemap.xml" in text

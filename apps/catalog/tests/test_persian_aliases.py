"""Searchable catalogue data without unrelated fuzzy matches."""

from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from apps.catalog.management.commands.seed_catalog import EXTENDED_PRODUCTS
from apps.catalog.models import Category, Product
from apps.catalog.search import normalize_for_search

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog(settings, monkeypatch):
    settings.DEBUG = True
    settings.DEMO_CATALOG = True
    monkeypatch.setitem(connection.settings_dict, "NAME", "test_premshop_ui_review")
    call_command("seed_catalog", extended=True, no_images=True, stdout=StringIO())
    return Product.objects.public()


@pytest.mark.parametrize(
    "query,slug",
    [
        ("پرپلکسیتی", "perplexity-pro"),
        ("پئو", "poe"),
        ("رانوی", "runway"),
        ("کرسر", "cursor-pro"),
        ("گیت هاب پرو", "github-pro"),
        ("رپلیت", "replit-core"),
        ("داکر", "docker-pro"),
        ("اپل موزیک", "apple-music"),
        ("دیزنی", "disney-plus"),
        ("کرانچی رول", "crunchyroll"),
        ("استیم", "steam-gift-card"),
        ("پلی استیشن", "playstation-gift-card"),
        ("ایکس باکس", "xbox-gift-card"),
        ("آمازون", "amazon-gift-card"),
        ("اینتلیج", "jetbrains-all"),
    ],
)
def test_persian_brand_spellings_find_the_product(catalog, query, slug):
    assert slug in catalog.search(query).values_list("slug", flat=True)


@pytest.mark.parametrize("category", ["gift", "ai", "dev", "media"])
def test_category_search_finds_every_product(catalog, category):
    group = Category.objects.get(slug=category)
    expected = set(catalog.filter(category=group).values_list("pk", flat=True))
    assert len(expected) == 6
    assert set(catalog.search(group.name).values_list("pk", flat=True)) == expected


@pytest.mark.parametrize(
    "query",
    ["وی پی ان", "فیلترشکن", "ویندوز", "ایرانسل", "اینستاگرام", "آنتی ویروس", "ماکروسافت آفیس"],
)
def test_aliases_do_not_answer_unrelated_searches(catalog, query):
    assert catalog.search("claude").exists()
    assert not catalog.search(query).exists()


def test_poe_alias_does_not_match_skin_search(catalog):
    assert not catalog.search("پوست").filter(slug="poe").exists()


@pytest.mark.parametrize(
    "query,slugs",
    [
        ("موسیقی", {"apple-music"}),
        ("فیلم", {"disney-plus"}),
        ("برنامه‌نویسی", {"cursor-pro", "github-pro", "replit-core", "docker-pro"}),
    ],
)
def test_use_words_find_relevant_samples(catalog, query, slugs):
    assert slugs <= set(catalog.search(query).values_list("slug", flat=True))


def test_seeded_aliases_have_persian_letters_and_preserve_operator_edits(catalog):
    assert len(EXTENDED_PRODUCTS) == 14
    for spec in EXTENDED_PRODUCTS:
        product = catalog.get(slug=spec["slug"])
        assert "aliases" not in product.fact_problems()
        assert product.aliases
    product.aliases = "نام انتخابی اپراتور"
    product.save()
    before = list(Product.objects.order_by("pk").values())
    call_command("seed_catalog", extended=True, no_images=True, stdout=StringIO())
    assert list(Product.objects.order_by("pk").values()) == before


def test_latin_names_and_original_persian_searches_still_work(catalog):
    for product in catalog:
        assert catalog.search(product.name).filter(pk=product.pk).exists()
    for query, slug in [("اسپاتیفای", "spotify-premium"), ("كلاود", "claude-pro")]:
        assert catalog.search(query).filter(slug=slug).exists()


def test_partial_category_save_does_not_index_an_unsaved_rename(catalog):
    group = Category.objects.get(slug="ai")
    before = list(group.products.order_by("pk").values_list("search_text", flat=True))
    group.name = "نام ذخیره نشده"
    group.sort_order = 17
    group.save(update_fields=["sort_order"])
    assert list(group.products.order_by("pk").values_list("search_text", flat=True)) == before


def test_category_rename_refreshes_search_text_for_all_statuses(catalog):
    group = Category.objects.get(slug="ai")
    products = list(group.products.all())
    group.name = "دسته آزمایشی تازه"
    group.save(update_fields=["name"])
    for product in products:
        product.refresh_from_db()
        assert normalize_for_search(group.name) in product.search_text
        assert "هوش مصنوعی" not in product.search_text or "هوش مصنوعی" in product.aliases
    assert set(Product.objects.search(group.name)) == set(products)


def test_partial_product_save_refreshes_searchable_fields(catalog):
    product = catalog.get(slug="perplexity-pro")
    product.aliases = "واژه تازه"
    product.save(update_fields=["aliases"])
    product.refresh_from_db()
    assert "واژه تازه" in product.search_text
    product.category = Category.objects.get(slug="gift")
    product.save(update_fields=["category"])
    product.refresh_from_db()
    assert "گیفت کارت" in product.search_text


@pytest.mark.parametrize("aliases", ["", " \n ", "perplexity", "!،؟. ۱۲۳ 123 ـَ"])
def test_invalid_aliases_block_readiness_and_new_publication(catalog, aliases):
    product = catalog.get(slug="perplexity-pro")
    product.status = Product.Status.DRAFT
    product.save()
    product.aliases = aliases
    product.save(update_fields=["aliases"])
    assert "aliases" in product.fact_problems()
    assert product.fact_problems()["aliases"] in product.publish_problems()
    product.status = Product.Status.ACTIVE
    with pytest.raises(ValidationError) as refused:
        product.clean()
    assert "aliases" in refused.value.message_dict


@pytest.mark.parametrize("aliases", ["", "perplexity", "!،؟"])
def test_unavailable_page_and_panel_follow_alias_readiness(catalog, client, admin_client, aliases):
    product = catalog.get(slug="perplexity-pro")
    product.status = Product.Status.UNAVAILABLE
    product.aliases = aliases
    product.save()
    assert client.get(product.get_absolute_url()).status_code == 404
    assert product.get_absolute_url() not in client.get("/sitemap.xml").content.decode()
    panel = admin_client.get(reverse("admin:catalog_product_change", args=[product.pk]))
    assert product.fact_problems()["aliases"] in panel.content.decode()
    response = admin_client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "publish", "_selected_action": [product.pk]},
    )
    assert response.status_code == 302
    product.refresh_from_db()
    assert product.status == Product.Status.UNAVAILABLE
    product.aliases = "پرپلکسیتی"
    product.save()
    assert client.get(product.get_absolute_url()).status_code == 200
    assert product.get_absolute_url() in client.get("/sitemap.xml").content.decode()
    assert not Product.objects.public().filter(pk=product.pk).exists()

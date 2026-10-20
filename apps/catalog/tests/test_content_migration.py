"""0020: today's descriptions and introductions, moved into the content model.

The review database held none of the cases the conversion has to get right:
every full description there equalled the short one, and every introduction
was empty. So the rows are built here, with each case in them, and taken
forwards, backwards and forwards again.
"""

from __future__ import annotations

import importlib
import re
from html import unescape

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template.defaultfilters import linebreaks_filter

from apps.core.richtext import render_markdown

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("catalog", "0019_content_sections_and_facts")]
AFTER = [("catalog", "0020_descriptions_into_the_content_model")]

DESCRIPTION = (
    "ChatGPT Plus روی اکانت خودتان.\n"
    "* این سطر فهرست نیست\n"
    "# و این تیتر نیست\n"
    "\n"
    "۱. نه شماره‌ی فهرست\n"
    "2) نه این یکی\n"
    "- نه نشانه\n"
    "با _زیرخط_، [کروشه](https://example.test)، <https://example.test> و &copy;\n"
    "| نه | جدول |\n"
    "\n\n"
    "پاراگراف آخر"
)
INTRO = (
    '<p>اشتراک‌های <b> هوش مصنوعی </b>، با <a href="https://openai.com/chatgpt/pricing">'
    "قیمت رسمی</a>.</p><script>alert(1)</script>"
    "<ul><li>تحویل روی اکانت خودتان</li><li>گیفت کارت</li></ul><p>1. نه فهرست</p>"
)

conversion = importlib.import_module(
    "apps.catalog.migrations.0020_descriptions_into_the_content_model"
)


def visible(html: str) -> str:
    """What a reader sees: the text, with every line or block break as one space."""
    text = re.sub(r"<br\s*/?>|</(?:p|li|h\d)>", " ", html)
    return " ".join(unescape(re.sub(r"<[^>]+>", "", text)).split())


def migrate(targets: list[tuple[str, str]]):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def old_rows():
    apps = migrate(BEFORE)
    Category = apps.get_model("catalog", "Category")
    Product = apps.get_model("catalog", "Product")
    category = Category.objects.create(name="هوش مصنوعی", slug="ai", intro_html=INTRO)
    described = Product.objects.create(
        category=category,
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        short_description="اشتراک ChatGPT Plus.",
        full_description=DESCRIPTION,
        delivery_type="on_customer_account",
    )
    # The page printed nothing for this one: its full text was its summary.
    Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        short_description="اشتراک Claude Pro.",
        full_description="اشتراک Claude Pro.",
        delivery_type="on_customer_account",
    )
    yield category.pk, described.pk
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_the_old_text_moves_in_and_comes_back_out(old_rows):
    category_pk, described_pk = old_rows

    apps = migrate(AFTER)
    [section] = apps.get_model("catalog", "ProductSection").objects.all()
    intro = apps.get_model("catalog", "Category").objects.get(pk=category_pk).intro

    assert (section.product_id, section.kind, section.title) == (described_pk, "free", "توضیحات")
    # What the page printed through `linebreaks`, it prints through Markdown.
    assert visible(render_markdown(section.body)) == visible(str(linebreaks_filter(DESCRIPTION)))
    shown = render_markdown(intro, heading_levels=())
    assert "<script" not in shown and "alert" not in shown
    assert '<a href="https://openai.com/chatgpt/pricing"' in shown
    assert "<strong>هوش مصنوعی</strong>" in shown
    assert "<li>گیفت کارت</li>" in shown and "<ol" not in shown

    apps = migrate(BEFORE)
    category = apps.get_model("catalog", "Category").objects.get(pk=category_pk)
    product = apps.get_model("catalog", "Product").objects.get(pk=described_pk)
    assert not apps.get_model("catalog", "ProductSection").objects.exists()
    assert (category.intro, category.intro_html) == ("", INTRO)
    assert product.full_description == DESCRIPTION

    apps = migrate(AFTER)
    assert apps.get_model("catalog", "ProductSection").objects.get().body == section.body


def test_going_back_leaves_a_section_the_operator_wrote(old_rows):
    _category_pk, described_pk = old_rows
    apps = migrate(AFTER)
    Section = apps.get_model("catalog", "ProductSection")
    Section.objects.create(product_id=described_pk, kind="free", title="توضیحات", body="جدید")

    apps = migrate(BEFORE)

    assert list(
        apps.get_model("catalog", "ProductSection").objects.values_list("body", flat=True)
    ) == ["جدید"]


def test_a_plain_text_introduction_keeps_its_paragraphs():
    """Since the HTML stopped running, the page has printed the field through
    `linebreaks`, so an introduction written since then is plain text."""
    markdown = conversion.intro_as_markdown("پاراگراف اول با * ستاره\n\nپاراگراف دوم")

    assert render_markdown(markdown, heading_levels=()) == (
        "<p>پاراگراف اول با * ستاره</p>\n<p>پاراگراف دوم</p>\n"
    )


@pytest.mark.parametrize(
    "href", ["javascript:alert(1)", "data:text/html,x", "//evil.test", "/\\evil.test"]
)
def test_a_link_the_renderer_would_refuse_keeps_only_its_text(href):
    markdown = conversion.intro_as_markdown(f'<p><a href="{href}">قیمت‌ها</a></p>')

    assert markdown == "قیمت‌ها"


def test_the_content_model_reverses_to_before_it_existed():
    """0019 only adds; taken back, the catalogue is as it was at 0018."""

    def tables() -> set[str]:
        with connection.cursor() as cursor:
            return set(connection.introspection.table_names(cursor))

    migrate([("catalog", "0018_latin_slugs_and_panel_copy")])
    assert not {"catalog_productsection", "catalog_productquestion"} & tables()

    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())

    assert {"catalog_productsection", "catalog_productquestion"} <= tables()

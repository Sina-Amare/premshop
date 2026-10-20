"""The product page built from the content model: facts, sections, questions, dates."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product, ProductQuestion, ProductSection
from apps.core.formatting import format_jalali

pytestmark = pytest.mark.django_db

Kind = ProductSection.Kind


@pytest.fixture
def product():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    obj = Product.objects.create(
        category=category,
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        short_description="اگر با ChatGPT رایگان به سقف می‌رسید، Plus این سقف را بالا می‌برد.",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        warranty=Product.Warranty.FULL_PERIOD,
        delivery_hours=12,
        iran_stance="پشتیبانی‌نشده",
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(product=obj, title="۱ ماهه", cost_price=1, sale_price=Decimal(1390000))
    return obj


def page(client, product) -> str:
    return client.get(reverse("product", args=[product.slug])).content.decode()


def section(product, kind, body="", **fields) -> ProductSection:
    return ProductSection.objects.create(product=product, kind=kind, body=body, **fields)


def headings(body: str) -> list[str]:
    return [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", body)]


# --- Sections ---------------------------------------------------------------


def test_sections_follow_the_operators_order_under_their_own_titles(client, product):
    section(product, Kind.AFTER_PAYMENT, "۱. پرداخت\n۲. فعال‌سازی", sort_order=3)
    section(product, Kind.BENEFITS, "- متن بلند", sort_order=1)
    section(product, Kind.INCLUDES, "- آپلود فایل", sort_order=2, title="امکانات Plus")

    titles = headings(page(client, product))

    assert titles.index("مزیت‌ها و کاربردها") < titles.index("امکانات Plus")
    assert titles.index("امکانات Plus") < titles.index("بعد از پرداخت")
    assert titles.index("بعد از پرداخت") < titles.index("پیش از خرید")


def test_a_section_is_restricted_markdown_with_h3_free(client, product):
    section(
        product,
        Kind.COMPARISON,
        "### زیرتیتر\n\n| | Go | Plus |\n|---|---|---|\n| تبلیغ | دارد | ندارد |\n\n<script>alert(1)</script>",
    )

    body = page(client, product)

    assert "<h3>زیرتیتر</h3>" in body
    assert "<th><bdi>Plus</bdi></th>" in body
    assert "<script>alert" not in body and "&lt;<bdi>script</bdi>&gt;" in body


def test_free_text_has_no_heading_unless_the_operator_writes_one(client, product):
    section(product, Kind.FREE, "## از خود متن\n\nپاراگراف")

    body = page(client, product)

    assert "متن آزاد" not in body
    # Untitled, its own heading takes the section's level instead of jumping to h3.
    assert "<h2>از خود متن</h2>" in body


def test_untitled_free_text_never_opens_the_outline_with_a_jump(client, product):
    """«تیتر ###» is what the field's help teaches, and untitled it followed
    the page's h1 as an h3."""
    section(product, Kind.FREE, "### تیتر درون متن\n\nپاراگراف\n\n#### زیرتیتر", sort_order=0)
    section(product, Kind.BENEFITS, "- متن بلند", sort_order=1)

    body = page(client, product)

    levels = [int(level) for level in re.findall(r"<h([1-6])[\s>]", body)]
    outline = levels[levels.index(1) :]
    assert all(b - a <= 1 for a, b in zip(outline, outline[1:], strict=False)), outline
    assert "<h2>تیتر درون متن</h2>" in body and "<h3>زیرتیتر</h3>" in body


def test_the_old_full_description_is_not_printed_beside_its_section(client, product):
    """0020 moved it into a «متن آزاد» section titled «توضیحات»."""
    product.full_description = "متنی که به بخش رفت"
    product.save()

    assert "متنی که به بخش رفت" not in page(client, product)


def test_a_section_image_carries_its_alt_text(client, product, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    shot = section(product, Kind.AFTER_PAYMENT, "۱. تنظیمات", image_alt="صفحه‌ی تنظیمات ChatGPT")
    ProductSection.objects.filter(pk=shot.pk).update(image="sections/settings.png")

    body = page(client, product)

    assert re.search(
        r'<img class="pdp__img" src="/media/sections/settings.png" alt="صفحه‌ی تنظیمات ChatGPT"',
        body,
    )


# --- «پیش از خرید» ------------------------------------------------------------


def before_buying(body: str) -> str:
    return body.split('id="before-buying"', 1)[1].split("</section>", 1)[0]


def test_before_buying_prints_its_facts_with_no_section_at_all(client, product):
    product.refund_note = "پس از فعال‌سازی، مبلغ برنمی‌گردد."
    product.save()

    part = before_buying(page(client, product))

    assert "پیش از خرید" in part
    assert "<dt>گارانتی</dt><dd>تا پایان دوره</dd>" in part
    assert "<dt>بازگشت وجه</dt><dd>پس از فعال‌سازی، مبلغ برنمی‌گردد.</dd>" in part
    assert "<dt>ایران</dt><dd>پشتیبانی‌نشده</dd>" in part


def test_before_buying_is_left_out_when_it_would_only_repeat_the_warranty(client, product):
    """The warranty is already beside the title and in the answers. A section
    holding nothing else reads as a placeholder, and nothing may link to it."""
    product.iran_stance = ""
    product.save()

    body = page(client, product)

    assert "پیش از خرید" not in body
    assert "#before-buying" not in body
    assert '<dt class="fact__k">گارانتی</dt><dd class="fact__v">تا پایان دوره</dd>' in body


@pytest.mark.parametrize("says", ["refund", "text"])
def test_before_buying_is_printed_when_it_says_more_than_the_warranty(client, product, says):
    product.iran_stance = ""
    product.refund_note = "پس از فعال‌سازی، مبلغ برنمی‌گردد." if says == "refund" else ""
    product.save()
    if says == "text":
        section(product, Kind.BEFORE_BUYING, "برای کار با API نیست.")

    body = page(client, product)

    assert body.count('id="before-buying"') == 1
    assert '<a href="#before-buying">گارانتی و شرط‌های خرید</a>' in body


def test_before_buying_puts_the_operators_text_after_the_facts_once(client, product):
    section(
        product,
        Kind.BEFORE_BUYING,
        "**این محصول برای شما نیست اگر** API لازم دارید.",
        sort_order=-5,
    )

    body = page(client, product)
    part = before_buying(body)

    assert part.index("<dt>گارانتی</dt>") < part.index(
        "<strong>این محصول برای شما نیست اگر</strong>"
    )
    assert body.count('id="before-buying"') == 1
    assert headings(body).count("پیش از خرید") == 1
    # Fixed below the other sections whatever its sort order.
    assert headings(body)[-2:] == ["پیش از خرید", "پرسش‌های متداول"]


@pytest.mark.parametrize("stance", ["موضوعیت ندارد", "موضوعيت ندارد.", " موضوعیت  ندارد "])
def test_a_product_the_iran_question_does_not_concern_says_nothing_about_iran(
    client, product, stance
):
    product.iran_stance = stance
    product.save()

    body = page(client, product)

    assert "ایران" not in body
    assert "موضوع" not in body


def test_the_stance_on_iran_is_beside_the_title_in_the_answers_and_before_buying(client, product):
    body = page(client, product)

    assert 'class="badge badge--key">ایران: پشتیبانی‌نشده</span>' in body
    assert (
        '<dt class="fact__k">از ایران</dt><dd class="fact__v"><a href="#before-buying">پشتیبانی‌نشده</a></dd>'
        in body
    )
    assert "<dt>ایران</dt><dd>پشتیبانی‌نشده</dd>" in before_buying(body)


# --- The answers index ---------------------------------------------------------


def answers(body: str) -> str:
    return body.split('class="card pdp__facts answers"', 1)[1].split("</dl>", 1)[0]


def test_each_answer_jumps_to_where_it_is_said_in_full(client, product):
    steps = section(product, Kind.AFTER_PAYMENT, "۱. فعال‌سازی")

    index = answers(page(client, product))

    assert (
        f'<dt class="fact__k">اکانت</dt><dd class="fact__v"><a href="#section-{steps.pk}">' in index
    )
    assert '<a href="#delivery">تا ۱۲ ساعت</a>' in index
    assert (
        '<dt class="fact__k">گارانتی</dt><dd class="fact__v"><a href="#before-buying">تا پایان دوره</a>'
        in index
    )
    assert "تمدید" not in index


def test_a_code_is_not_called_an_account(client, product):
    product.delivery_type = Product.DeliveryType.GIFT_CARD
    product.save()

    index = answers(page(client, product))

    assert '<dt class="fact__k">نوع تحویل</dt><dd class="fact__v">کد گیفت کارت</dd>' in index
    assert "اکانت" not in index


# --- Questions --------------------------------------------------------------------


def test_the_products_questions_come_before_the_shops_own(client, product):
    ProductQuestion.objects.create(
        product=product,
        question="Codex روی Android هم کار می‌کنه؟",
        answer="بله، به‌صورت **پیش‌نمایش**.",
        sort_order=2,
    )
    ProductQuestion.objects.create(
        product=product, question="چندماهه هم دارید؟", answer="نه.", sort_order=1
    )

    body = page(client, product)
    faq = body.split('id="questions-title"', 1)[1]

    assert faq.index("چندماهه هم دارید؟") < faq.index("<bdi>Codex</bdi> روی <bdi>Android</bdi>")
    assert faq.index("<bdi>Android</bdi>") < faq.index('<dt id="delivery">')
    assert "<strong>پیش‌نمایش</strong>" in faq


# --- Dates the page states ------------------------------------------------------------


def test_the_price_date_is_the_oldest_confirmation_among_available_plans(client, product):
    now = timezone.now()
    Plan.objects.filter(product=product).update(price_confirmed_at=now - timedelta(days=9))
    Plan.objects.create(product=product, title="۳ ماهه", cost_price=1, sale_price=3900000)
    Plan.objects.filter(title="۳ ماهه").update(price_confirmed_at=now)
    # A plan nobody can buy does not hold the date back.
    Plan.objects.create(
        product=product, title="قدیمی", cost_price=1, sale_price=1, is_available=False
    )
    Plan.objects.filter(title="قدیمی").update(price_confirmed_at=now - timedelta(days=90))

    response = client.get(reverse("product", args=[product.slug]))

    oldest = now - timedelta(days=9)
    assert response.context["price_checked_since"] == oldest
    assert f"قیمت‌ها بررسی‌شده از {format_jalali(oldest)}" in response.content.decode()


def test_a_price_nobody_has_confirmed_leaves_the_date_off(client, product):
    Plan.objects.create(product=product, title="۳ ماهه", cost_price=1, sale_price=3900000)
    Plan.objects.filter(title="۱ ماهه").update(price_confirmed_at=None)

    response = client.get(reverse("product", args=[product.slug]))

    assert response.context["price_checked_since"] is None
    assert "قیمت‌ها بررسی‌شده" not in response.content.decode()


def test_the_listings_carry_the_oldest_date_of_the_plans_they_show(client, product):
    week_ago = timezone.now() - timedelta(days=7)
    Plan.objects.filter(product=product).update(price_confirmed_at=week_ago)
    other = Product.objects.create(
        category=product.category,
        name="Claude Pro",
        slug="claude-pro",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(product=other, title="۱ ماهه", cost_price=1, sale_price=900000)

    for url in (reverse("home"), reverse("category", args=["ai"])):
        assert client.get(url).context["prices_checked_since"] == week_ago, url


def test_the_last_check_of_the_facts_is_dated(client, product):
    assert "آخرین بررسی" not in page(client, product)

    product.facts_checked_on = date(2026, 10, 19)
    product.save()

    assert "آخرین بررسی: ۲۷ مهر ۱۴۰۵" in page(client, product)


def test_the_buy_box_links_to_the_conditions(client, product):
    body = page(client, product)

    assert (
        '<a href="#before-buying">گارانتی و شرط‌های خرید</a>'
        in body.split('class="pdp__buy"', 1)[1]
    )

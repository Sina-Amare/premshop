"""The rate sheet on the category and search pages: one deadline or one per row,
the plan column's name, the order of a full sheet, and where the way to ask sits."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product
from apps.core import support

pytestmark = pytest.mark.django_db


def stock(category: Category, slug: str, **plan) -> Product:
    product = Product.objects.create(
        category=category,
        name=slug,
        slug=slug,
        delivery_type=plan.pop("delivery_type", Product.DeliveryType.READY_ACCOUNT),
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(
        product=product, title="۱ ماهه", cost_price=Decimal(1), sale_price=Decimal(200000), **plan
    )
    return product


@pytest.fixture
def ai():
    return Category.objects.create(name="هوش مصنوعی", slug="ai", sort_order=0)


def page(client, category: Category) -> str:
    return client.get(reverse("category", args=[category.slug])).content.decode()


def test_discounts_that_end_together_print_one_deadline_in_the_head(client, ai):
    end = timezone.now() + timedelta(days=3)
    for slug in ("one", "two"):
        stock(ai, slug, promo_price=Decimal(150000), promo_ends_at=end)

    body = page(client, ai)

    assert body.count('class="sheet__note"') == 1
    assert "rs__clock" not in body
    assert body.count('class="badge badge--time countdown"') == 1


def test_discounts_that_end_apart_each_carry_their_own(client, ai):
    for slug, days in (("one", 3), ("two", 5)):
        stock(
            ai,
            slug,
            promo_price=Decimal(150000),
            promo_ends_at=timezone.now() + timedelta(days=days),
        )
    stock(ai, "full-price")

    body = page(client, ai)

    assert "sheet__note" not in body
    assert body.count('<span class="rs__clock">') == 2, "the full-price row has none"


def test_a_gift_card_sheet_lists_amounts_not_periods(client, ai):
    gifts = Category.objects.create(name="گیفت کارت", slug="gift")
    stock(gifts, "card", delivery_type=Product.DeliveryType.GIFT_CARD)
    stock(ai, "chat")

    assert '<th scope="col">مبلغ کارت</th>' in page(client, gifts)
    assert '<th scope="col">مدت اشتراک</th>' in page(client, ai), "positive control"


def test_a_search_miss_offers_telegram_first_then_every_department_in_order(client, ai):
    gifts = Category.objects.create(name="گیفت کارت", slug="gift", sort_order=1)
    stock(gifts, "card", delivery_type=Product.DeliveryType.GIFT_CARD)
    stock(ai, "chat")
    stock(ai, "art")

    body = client.get(reverse("search"), {"q": "xbox"}).content.decode()

    ask = body.index(support.details()["telegram_url"], body.index('id="main"'))
    assert ask < body.index('class="sheet sheet--doc ratesheet"'), "the way to ask comes first"
    titles = body.split('class="rs-group__title"')[1:]
    assert [t.split(">", 1)[1].split("<", 1)[0] for t in titles] == ["هوش مصنوعی", "گیفت کارت"]
    assert body.index(">art<") < body.index(">chat<"), "by name inside a department"
    assert "۳ سرویس در ۲ دسته" in body


def test_an_empty_department_offers_telegram_where_the_list_would_be(client, ai):
    body = page(client, ai)
    main = body[body.index('id="main"') :]

    assert "در این دسته فعلاً سرویسی نداریم" in main
    assert support.details()["telegram_url"] in main
    assert support.details()["hours"] in main


def test_the_comparison_needs_two_products_and_speaks_to_the_buyer(client, ai):
    stock(ai, "chat", delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT)
    assert 'class="cmp"' not in page(client, ai), "one product compares with nothing"

    stock(ai, "art")
    body = page(client, ai)

    assert 'class="cmp"' in body
    assert "<td>روی اکانت خودتان</td>" in body and "<td>اکانت آماده</td>" in body
    assert "شخصی" not in body, "the panel's label, not the buyer's"


def test_a_discount_with_no_end_keeps_one_date_off_the_head(client, ai):
    end = timezone.now() + timedelta(days=3)
    for slug in ("one", "two"):
        stock(ai, slug, promo_price=Decimal(150000), promo_ends_at=end)
    stock(ai, "open", promo_price=Decimal(150000))

    body = page(client, ai)

    assert "sheet__note" not in body, "«تخفیف‌ها تا …» would be false for the open-ended one"
    assert body.count('<span class="rs__clock">') == 2


def test_a_department_prints_its_questions_and_an_empty_one_the_rest_of_the_shop(client, ai):
    product = stock(ai, "chat")
    product.questions.create(question="روی چند دستگاه؟", answer="روی یک دستگاه.")
    stock(ai, "art")
    empty = Category.objects.create(name="فیلم", slug="media", sort_order=1)

    asked = page(client, ai)
    elsewhere = page(client, empty)

    assert "پرسش‌های این دسته" in asked and "روی چند دستگاه؟" in asked
    assert asked.count('class="catqa__group"') == 1, "only the product that has questions"
    assert elsewhere.index("در این دسته فعلاً سرویسی نداریم") < elsewhere.index(">chat<")


def test_one_search_hit_sits_above_the_rest_of_its_department(client, ai):
    stock(ai, "claude")
    stock(ai, "art")
    stock(Category.objects.create(name="گیفت کارت", slug="gift"), "card")

    body = client.get(reverse("search"), {"q": "claude"}).content.decode()

    assert body.index(">claude<") < body.index("سرویس‌های دیگر هوش مصنوعی") < body.index(">art<")
    assert ">card<" not in body, "another department is not the hit's neighbour"

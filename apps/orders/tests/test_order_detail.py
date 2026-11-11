import re
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product
from apps.core.formatting import to_persian_digits
from apps.orders import services
from apps.orders.models import Order
from apps.orders.tests.test_delivery import deliver
from apps.orders.tests.test_input_clock import act
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import cancel, paid_order
from apps.orders.tests.test_replacement import claim
from apps.payments.models import Refund
from apps.payments.tests.test_refund_execution import execute

pytestmark = pytest.mark.django_db(transaction=True)

CASES = [
    ("unpaid", "منتظر پرداخت", False),
    ("overdue", "مهلت پرداخت تمام شده است", False),
    ("queued", "در صف انجام", False),
    ("waiting", "اطلاعات فعال‌سازی لازم است", True),
    ("delivered", "تحویل داده شده", False),
    ("claimed", "درخواست تعویض در حال بررسی است", False),
    ("cancelled", "سفارش لغو شده است", False),
    ("owed", "سفارش لغو شده است", False),
    ("refunded", "بازپرداخت انجام شده است", False),
    ("mixed", "اطلاعات فعال‌سازی لازم است", True),
    ("mixed_refund", "بخشی از سفارش لغو شده است", False),
    ("empty", "وضعیت سفارش", False),
]


def order_for_state(buyer, plan, operator, state):
    if state == "empty":
        return Order.objects.create(user=buyer, subtotal=0, total_amount=0)
    lines = [services.OrderLine(plan, 2 if state in ("mixed", "mixed_refund") else 1)]
    if state in ("unpaid", "overdue", "cancelled"):
        order = services.place_order(user=buyer, lines=lines)
        if state == "overdue":
            Order.objects.filter(pk=order.pk).update(unpaid_expires_at=timezone.now())
        elif state == "cancelled":
            services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
        return order
    order, items = paid_order(buyer, operator, lines)
    if state in ("waiting", "mixed"):
        act(services.request_input, items[0], operator)
    elif state == "mixed_refund":
        deliver(items[0], operator)
        cancel(items[1], operator)
    elif state in ("delivered", "claimed"):
        deliver(items[0], operator)
        if state == "claimed":
            claim(items[0], operator)
    elif state in ("owed", "refunded"):
        cancel(items[0], operator)
        if state == "refunded":
            execute(order, list(Refund.objects.filter(order_item__order=order)), operator)
    return order


@pytest.mark.parametrize("state,heading,needs_input", CASES)
def test_order_detail_shows_the_next_step_for_each_state(
    client, buyer, plan, operator, state, heading, needs_input
):
    order = order_for_state(buyer, plan, operator, state)
    client.force_login(buyer)
    response = client.get(reverse("order-detail", args=[order.order_number]))
    body = response.content.decode()
    assert response.status_code == 200
    assert '<section class="card order-card order-next"' in body
    assert f'id="order-next-title">{heading}</h2>' in body
    assert ("اطلاعات فعال‌سازی را در تلگرام برای پشتیبانی بفرستید." in body) is needs_input
    assert ("data-input-request" in body) is needs_input
    assert ("بله، لغو سفارش" in body) is (state in ("unpaid", "overdue"))
    if state in ("queued", "delivered", "claimed", "refunded"):
        assert "لازم نیست کاری انجام دهید." in body
    if state in ("cancelled", "owed"):
        assert "برای پیگیری پرداخت یا بازپرداخت، به پشتیبانی پیام بدهید." in body
    assert "تماس بگیرید" not in body  # support is a chat and an email address, not a phone
    assert 'name="customer_input"' not in body and "expected_event_id" not in body


def test_order_detail_keeps_purchase_help_and_quiet_records_in_reading_order(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "unpaid")
    Order.objects.filter(pk=order.pk).update(
        policy_snapshot={"terms": {"title": "قوانین فروشگاه", "body": "متن ثبت‌شده"}}
    )
    client.force_login(buyer)
    response = client.get(reverse("order-detail", args=[order.order_number]))
    body = response.content.decode()
    positions = [
        body.index(marker)
        for marker in (
            'id="order-next"',
            'id="order-purchase"',
            'id="order-help"',
            'class="order-records"',
        )
    ]
    assert positions == sorted(positions)
    assert '<details class="order-record" id="order-conditions">' in body
    assert '<details class="order-record" id="order-history">' in body
    assert '<details class="order-record" id="order-tracking">' in body
    assert reverse("order-tracking", args=[order.tracking_token]) in body
    assert reverse("order-invoice", args=[order.order_number]) in body
    assert order.support_identifier.grouped_token in body
    assert "متن ثبت‌شده" in body
    assert body.count("data-order-total") == 1


def page(client, buyer, order):
    client.force_login(buyer)
    return client.get(reverse("order-detail", args=[order.order_number])).content.decode()


def headings(body, level):
    """The text of every heading of one level in the account area, footer excluded."""
    main = body.split('class="acct__main"', 1)[1].split("</main>", 1)[0]
    found = re.findall(rf"<h{level}[^>]*>(.*?)</h{level}>", main, re.S)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip() for text in found]


def second_plan():
    category = Category.objects.create(name="دسته‌ی دوم", slug="second")
    product = Product.objects.create(
        category=category,
        name="سرویس دوم",
        slug="second",
        status=Product.Status.ACTIVE,
        warranty=Product.Warranty.DAYS_7,
    )
    return Plan.objects.create(
        product=product,
        title="سه ماهه",
        sale_price=Decimal("300000"),
        cost_price=Decimal("150000"),
        duration_days=90,
        max_quantity=3,
        input_type=Plan.InputType.NONE,
        telegram_required=False,
    )


def test_subtotal_row_appears_only_beside_a_discount(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "unpaid")
    body = page(client, buyer, order)
    assert "جمع محصولات" not in body
    assert body.count("data-order-total") == 1
    Order.objects.filter(pk=order.pk).update(
        discount_amount=Decimal("10000"),
        total_amount=order.total_amount - Decimal("10000"),
        discount_snapshot={"code": "SAVE"},
    )
    body = page(client, buyer, order)
    assert "جمع محصولات" in body and "تخفیف" in body and "SAVE" in body
    assert body.count("data-order-total") == 1


def test_product_terms_and_shop_pages_share_one_disclosure(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "unpaid")
    Order.objects.filter(pk=order.pk).update(
        policy_snapshot={"terms": {"title": "قوانین فروشگاه", "body": "متن ثبت‌شده"}}
    )
    body = page(client, buyer, order)
    assert body.count("<summary>شرایط هنگام خرید</summary>") == 1
    assert "شرایط زمان خرید" not in body and 'id="order-policies"' not in body
    outside, inside = body.split('id="order-conditions"', 1)
    inside = inside.split("</details>\n<details", 1)[0]
    assert "گارانتی" not in outside and "گارانتی" in inside
    assert "قوانین فروشگاه" in inside and "متن ثبت‌شده" in inside
    assert plan.product.name in inside


@pytest.mark.parametrize("state,heading,needs_input", CASES)
def test_page_headings_are_the_card_titles_and_nothing_else(
    client, buyer, plan, operator, state, heading, needs_input
):
    order = order_for_state(buyer, plan, operator, state)
    body = page(client, buyer, order)
    assert headings(body, 1) == [f"سفارش {to_persian_digits(order.order_number)}"]
    assert headings(body, 2) == [heading, "محصولات و مبلغ سفارش"]


def test_category_names_show_only_for_several_categories_and_are_not_headings(
    client, buyer, plan, operator
):
    single = page(client, buyer, order_for_state(buyer, plan, operator, "unpaid"))
    assert "order-category__name" not in single
    assert headings(single, 3) == [plan.product.name]
    other = User.objects.create_user("other@example.test")  # one unpaid order per customer
    order = services.place_order(
        user=other, lines=[services.OrderLine(plan, 1), services.OrderLine(second_plan(), 1)]
    )
    body = page(client, other, order)
    names = re.findall(r'<p class="order-category__name">(.*?)</p>', body)
    assert names == ["نمونه", "دسته‌ی دوم"]
    assert headings(body, 3) == ["سرویس نمونه", "سرویس دوم"]
    assert not set(names) & set(headings(body, 2) + headings(body, 3))


HELP_OPEN = {
    "unpaid": True,
    "overdue": True,
    "waiting": True,
    "mixed": True,
    "cancelled": True,
    "owed": True,
    "mixed_refund": True,
    "empty": True,
    "queued": False,
    "delivered": False,
    "claimed": False,
    "refunded": False,
}


@pytest.mark.parametrize("state,heading,needs_input", CASES)
def test_support_is_open_where_the_card_points_to_it_and_closed_otherwise(
    client, buyer, plan, operator, state, heading, needs_input
):
    order = order_for_state(buyer, plan, operator, state)
    body = page(client, buyer, order)
    tag = re.search(r'<details[^>]*id="order-help"[^>]*>', body)
    assert tag, "support is a disclosure"
    assert (" open" in tag.group(0)) is HELP_OPEN[state]
    assert "<summary>پشتیبانی سفارش</summary>" in body
    if 'href="#order-help"' in body:
        assert " open" in tag.group(0), "a link must land on an open section"


@pytest.mark.parametrize("state,heading,needs_input", CASES)
def test_the_next_card_names_one_action_or_says_none_is_needed(
    client, buyer, plan, operator, state, heading, needs_input
):
    order = order_for_state(buyer, plan, operator, state)
    body = page(client, buyer, order)
    card = body.split('id="order-next"', 1)[1].split("</section>", 1)[0]
    actions = len(re.findall(r"<(?:a|button)\b", card))
    assert actions + ("لازم نیست کاری انجام دهید." in card) == 1

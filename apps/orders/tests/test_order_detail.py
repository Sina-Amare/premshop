import re
from datetime import timedelta
from decimal import Decimal

import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product
from apps.core import support
from apps.core.formatting import format_jalali, format_toman, to_persian_digits
from apps.orders import selectors, services, telegram_panel
from apps.orders.models import Order, OrderItem
from apps.orders.tests.test_delivery import deliver
from apps.orders.tests.test_input_clock import act
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import cancel, paid_order
from apps.orders.tests.test_replacement import claim, reject
from apps.payments.models import Refund
from apps.payments.tests.test_refund_execution import execute

pytestmark = pytest.mark.django_db(transaction=True)

# The research note's state table: pill, badge, a fragment of the heading, and whether
# writing to support is the card's action (2026-11-11-status-clarity.md).
CASES = [
    ("unpaid", "در انتظار پرداخت", "time", "پرداخت تا", True),
    ("overdue", "پایان مهلت پرداخت", "neutral", "پرداختی برای این سفارش ثبت نشد", False),
    ("queued", "در حال آماده‌سازی", "progress", "تحویل", False),
    ("late", "تأخیر در تحویل", "danger", "گذشت", True),
    ("waiting", "در انتظار اطلاعات شما", "time", "اطلاعات فعال‌سازی", True),
    ("delivered", "تحویل‌شده", "ok", "تحویل شد", True),
    ("claimed", "بررسی درخواست تعویض", "progress", "درخواست تعویض", False),
    ("rejected", "تحویل‌شده", "ok", "درخواست تعویض در", True),
    ("cancelled", "لغوشده", "danger", "لغو کردید", False),
    ("operator_cancelled", "لغوشده", "danger", "پشتیبانی این سفارش را", True),
    ("expired", "پایان مهلت پرداخت", "neutral", "پرداختی برای این سفارش ثبت نشد", False),
    ("owed", "در انتظار بازگشت وجه", "time", "به شما برمی‌گردانیم", True),
    ("refunded", "بازگشت وجه تکمیل‌شده", "neutral", "به شما برگرداندیم", False),
    ("mixed", "در انتظار اطلاعات شما", "time", "اطلاعات فعال‌سازی", True),
    ("mixed_refund", "در انتظار بازگشت وجه", "time", "به شما برمی‌گردانیم", True),
    ("empty", "لغوشده", "danger", "این سفارش محصولی ندارد", False),
]


def order_for_state(buyer, plan, operator, state):
    if state == "empty":
        return Order.objects.create(user=buyer, subtotal=0, total_amount=0)
    lines = [services.OrderLine(plan, 2 if state in ("mixed", "mixed_refund") else 1)]
    if state in ("unpaid", "overdue", "expired", "cancelled", "operator_cancelled"):
        order = services.place_order(user=buyer, lines=lines)
        if state in ("overdue", "expired"):
            lapsed = timezone.now() - timedelta(minutes=1)
            Order.objects.filter(pk=order.pk).update(unpaid_expires_at=lapsed)
            if state == "expired":
                services.expire_unpaid_orders()
        elif state in ("cancelled", "operator_cancelled"):
            if state == "operator_cancelled":
                services.cancel_unpaid_order(order_id=order.pk, actor=operator, note="ناموجود")
            else:
                services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
        return order
    order, items = paid_order(buyer, operator, lines)
    if state in ("waiting", "mixed"):
        act(services.request_input, items[0], operator)
    elif state == "mixed_refund":
        deliver(items[0], operator)
        cancel(items[1], operator)
    elif state == "late":
        OrderItem.objects.filter(pk=items[0].pk).update(
            due_at=timezone.now() - timedelta(minutes=5)
        )
    elif state in ("delivered", "claimed", "rejected"):
        deliver(items[0], operator)
        if state in ("claimed", "rejected"):
            claim(items[0], operator)
        if state == "rejected":
            reject(items[0], operator)
    elif state in ("owed", "refunded"):
        cancel(items[0], operator)
        if state == "refunded":
            execute(order, list(Refund.objects.filter(order_item__order=order)), operator)
    return order


def page(client, buyer, order):
    client.force_login(buyer)
    return client.get(reverse("order-detail", args=[order.order_number])).content.decode()


def card_of(body):
    return body.split('id="order-next"', 1)[1].split("</section>", 1)[0]


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def headings(body, level):
    """The text of every heading of one level in the account area, footer excluded."""
    main = body.split('class="acct__main"', 1)[1].split("</main>", 1)[0]
    found = re.findall(rf"<h{level}[^>]*>(.*?)</h{level}>", main, re.S)
    return [text(found_text) for found_text in found]


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


@pytest.mark.parametrize("state,pill,badge,fragment,contact", CASES)
def test_each_state_shows_its_pill_its_fact_and_only_its_own_action(
    client, buyer, plan, operator, state, pill, badge, fragment, contact
):
    order = order_for_state(buyer, plan, operator, state)
    body = page(client, buyer, order)
    card = card_of(body)
    assert f'<span class="badge badge--{badge}"' in card and pill in card
    heading = re.search(r'id="order-next-title">(.*?)</h2>', card, re.S)
    assert heading and fragment in text(heading[1]), heading
    # Support sits in the card only when writing to it is the action.
    assert ("data-support-contact" in card) is contact
    assert "#order-help" not in card
    assert body.count('id="support-identifier"') <= 1
    assert "تماس بگیرید" not in body
    assert ("بله، لغو سفارش" in body) is (state in ("unpaid", "overdue"))
    # A pill is read, never followed.
    assert not re.search(r'<a [^>]*class="badge', body)
    assert 'name="customer_input"' not in body and "expected_event_id" not in body


@pytest.mark.parametrize("state,pill,badge,fragment,contact", CASES)
def test_page_headings_are_the_card_titles_and_nothing_else(
    client, buyer, plan, operator, state, pill, badge, fragment, contact
):
    order = order_for_state(buyer, plan, operator, state)
    body = page(client, buyer, order)
    assert headings(body, 1) == [f"سفارش {to_persian_digits(order.order_number)}"]
    titles = headings(body, 2)
    assert len(titles) == 2 and fragment in titles[0]
    assert titles[1] == "محصولات و مبلغ سفارش"


def test_a_cancelled_unpaid_order_says_no_payment_was_recorded_and_offers_no_support(
    client, buyer, plan, operator
):
    card = card_of(page(client, buyer, order_for_state(buyer, plan, operator, "cancelled")))
    assert "پرداختی برای آن ثبت نشده بود." in card
    assert support.details()["telegram_url"] not in card
    assert "دیدن محصولات" in card


def test_an_overdue_order_offers_a_new_order_and_no_payment(client, buyer, plan, operator):
    card = card_of(page(client, buyer, order_for_state(buyer, plan, operator, "overdue")))
    assert "ثبت سفارش جدید" in card and plan.product.get_absolute_url() in card
    assert "پرداخت آنلاین" not in card and "data-support-contact" not in card


def test_a_refund_owed_shows_the_ledgers_amount_and_asks_for_a_card_in_the_customers_name(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "owed")
    refund = Refund.objects.get(order_item__order=order)
    body = page(client, buyer, order)
    card = card_of(body)
    figure = format_toman(refund.amount, with_unit=False)
    assert figure in card and "خرید سرویس نمونه لغو شد، چون نتوانستیم آن را تهیه کنیم." in text(card)
    assert "شماره‌ی کارت یا شبای به نام خودتان" in card and "تا یک روز کاری" in card
    assert "هیچ‌وقت رمز کارت یا رمز پویا را از شما نمی‌خواهد." in card
    owed_row = body.split('data-money="owed"', 1)[1].split("</div>", 1)[0]
    assert figure in owed_row


def test_a_refund_paid_shows_its_amount_and_date_but_never_where_it_went(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "refunded")
    refund = Refund.objects.get(order_item__order=order)
    body = page(client, buyer, order)
    assert format_toman(refund.amount, with_unit=False) in card_of(body)
    assert 'data-money="returned"' in body
    for staff_only in (refund.destination_last4, refund.bank_ref, "6037", "transfer-private"):
        assert staff_only not in body and to_persian_digits(staff_only) not in body


def test_a_paid_item_a_code_covered_fully_owes_nothing_and_never_reads_as_free(
    client, buyer, plan, operator
):
    _, items = paid_order(buyer, operator, [services.OrderLine(plan, 1)])
    OrderItem.objects.filter(pk=items[0].pk).update(discount_share=items[0].price_snapshot)
    items[0].refresh_from_db()
    cancel(items[0], operator)
    order = items[0].order
    assert not Refund.objects.filter(order_item__order=order).exists()
    card = card_of(page(client, buyer, order))
    assert "این لغو بازگشت وجه ندارد." in card
    assert "پرداختی برای آن ثبت نشده بود" not in card and "رایگان" not in card


def test_a_queued_item_past_its_delivery_time_reads_as_late_and_points_to_support(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "queued")
    order.items.update(due_at=timezone.now() - timedelta(minutes=5))
    card = card_of(page(client, buyer, order))
    assert '<span class="badge badge--danger"' in card and "تأخیر در تحویل" in card
    assert "گذشت" in card and support.details()["telegram_url"] in card


def test_a_delivered_item_names_its_subscription_and_warranty_ends(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "delivered")
    end = timezone.localdate() + timedelta(days=30)
    order.items.update(expires_at=end)
    card = card_of(page(client, buyer, order))
    assert "اشتراک: تا" in card and "گارانتی: تا پایان" in card
    # Inside the warranty, what to send waits behind one quiet question.
    assert "<summary>مشکلی هست؟</summary>" in card and "data-support-contact" in card


def test_a_delivered_item_past_its_warranty_offers_no_support(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "delivered")
    order.items.update(delivered_at=timezone.now() - timedelta(days=10))
    card = card_of(page(client, buyer, order))
    assert "مشکلی هست؟" not in card and "data-support-contact" not in card
    assert 'id="support-identifier"' in page(client, buyer, order)


def two_products(buyer, plan, operator):
    lines = [services.OrderLine(plan, 1), services.OrderLine(second_plan(), 1)]
    return paid_order(buyer, operator, lines)[1]


def line_money(body):
    return [text(found) for found in re.findall(r"<p data-line-money>(.*?)</p>", body, re.S)]


def test_two_refunds_owed_name_each_product_with_its_own_reason_and_amount(
    client, buyer, plan, operator
):
    with time_machine.travel(timezone.now() - timedelta(days=22), tick=False):
        items = two_products(buyer, plan, operator)
        act(services.request_input, items[1], operator)
    cancel(items[0], operator)
    cancel(items[1], operator, reason=OrderItem.CancelReason.INPUT_TIMEOUT)
    body = page(client, buyer, items[0].order)
    card = text(card_of(body))
    assert format_toman(Decimal(500000), with_unit=False) in card
    assert "خرید سرویس نمونه لغو شد، چون نتوانستیم آن را تهیه کنیم." in card
    assert "خرید سرویس دوم لغو شد، چون اطلاعات فعال‌سازی تا ۲۱ روز به دستمان نرسید." in card
    lines = line_money(body)
    assert len(lines) == 2
    for amount in (200000, 300000):
        assert any(format_toman(Decimal(amount), with_unit=False) in line for line in lines)


def test_refunds_sent_on_different_days_each_keep_their_own_day(client, buyer, plan, operator):
    earlier = timezone.now() - timedelta(days=10)
    with time_machine.travel(earlier, tick=False):
        items = two_products(buyer, plan, operator)
        cancel(items[0], operator)
        cancel(items[1], operator)
        execute(items[0].order, [Refund.objects.get(order_item=items[0])], operator)
    execute(items[0].order, [Refund.objects.get(order_item=items[1])], operator)
    body = page(client, buyer, items[0].order)
    card = text(card_of(body))
    days = {200000: format_jalali(earlier), 300000: format_jalali(timezone.now())}
    assert not any(day in card for day in days.values())
    lines = line_money(body)
    for amount, day in days.items():
        assert any(
            format_toman(Decimal(amount), with_unit=False) in line and day in line for line in lines
        ), lines


def test_details_awaited_for_two_products_promise_the_longer_time(client, buyer, plan, operator):
    items = two_products(buyer, plan, operator)
    for item, hours in zip(items, (2, 24), strict=True):
        act(services.request_input, item, operator)
        OrderItem.objects.filter(pk=item.pk).update(
            due_at=None,
            sla_paused_at=None,
            product_snapshot={**item.product_snapshot, "delivery_hours": hours},
        )
    card = text(card_of(page(client, buyer, items[0].order)))
    assert "تا ۲۴ ساعت" in card and "تا ۲ ساعت" not in card


def test_products_delivered_weeks_apart_each_keep_their_own_dates(client, buyer, plan, operator):
    items = two_products(buyer, plan, operator)
    for item in items:
        deliver(item, operator)
    weeks_ago = timezone.now() - timedelta(days=20)
    OrderItem.objects.filter(pk=items[0].pk).update(
        delivered_at=weeks_ago, expires_at=timezone.localdate() + timedelta(days=10)
    )
    OrderItem.objects.filter(pk=items[1].pk).update(
        expires_at=timezone.localdate() + timedelta(days=90)
    )
    body = page(client, buyer, items[0].order)
    card = card_of(body)
    # One date cannot speak for both, and the second is still inside its warranty.
    assert "تحویل در" not in text(card) and "<summary>مشکلی هست؟</summary>" in card
    purchase = text(body.split('id="order-purchase"', 1)[1].split('class="order-records"', 1)[0])
    for day in (weeks_ago, timezone.now()):
        assert f"تحویل در {format_jalali(day)}" in purchase


def test_a_replacement_request_and_its_rejection_carry_their_dates(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "claimed")
    today = format_jalali(timezone.now())
    assert f"در {today} ثبت شد" in text(card_of(page(client, buyer, order)))
    reject(order.items.get(), operator)
    card = text(card_of(page(client, buyer, order)))
    assert f"درخواست تعویض در {today} رد شد" in card
    assert "دلیلش را از پشتیبانی بپرسید." in card


def test_tracking_and_the_order_page_agree_on_a_lapsed_payment_window(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "expired")
    body = client.get(reverse("order-tracking", args=[order.tracking_token])).content.decode()
    assert "پایان مهلت پرداخت" in body and "لغوشده" not in body


def test_the_bot_says_where_to_send_a_card_number_and_warns_about_codes(buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "owed")
    screen, _ = telegram_panel.render(buyer, f"o:{order.order_number}")
    assert support.details()["telegram"] in screen
    assert "هیچ‌وقت رمز کارت یا رمز پویا را از شما نمی‌خواهد." in screen


def test_the_account_card_finds_an_open_order_behind_many_newer_closed_ones(buyer, plan, operator):
    owed = order_for_state(buyer, plan, operator, "owed")
    for _ in range(21):
        order_for_state(buyer, plan, operator, "empty")
    assert selectors.current_order(buyer) == owed


def test_tracking_shows_pills_but_never_money(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "owed")
    refund = Refund.objects.get(order_item__order=order)
    body = client.get(reverse("order-tracking", args=[order.tracking_token])).content.decode()
    assert '<span class="badge badge--danger"' in body and "لغوشده" in body
    assert format_toman(refund.amount, with_unit=False) not in body
    assert "به شما برمی‌گردانیم" not in body


@pytest.mark.parametrize("state", ["mixed_refund", "mixed", "owed", "queued"])
def test_the_page_the_list_the_account_card_and_the_bot_lead_with_the_same_state(
    client, buyer, plan, operator, state
):
    order = order_for_state(buyer, plan, operator, state)
    badge = re.search(
        r'<span class="badge[^"]*"[^>]*>(.*?)</span>', card_of(page(client, buyer, order)), re.S
    )
    assert badge
    label = text(badge[1])
    listed = client.get(reverse("order-list")).content.decode()
    header = re.search(r'<header class="order-heading">(.*?)</header>', listed, re.S)
    assert header and label in text(header[1])
    account = client.get(reverse("account")).content.decode()
    assert label in account.split('id="current-order-title"', 1)[1].split("</section>", 1)[0]
    screen, _ = telegram_panel.render(buyer, f"o:{order.order_number}")
    assert f"وضعیت: {label}" in screen


def test_order_detail_keeps_purchase_and_quiet_records_in_reading_order(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "unpaid")
    Order.objects.filter(pk=order.pk).update(
        policy_snapshot={"terms": {"title": "قوانین فروشگاه", "body": "متن ثبت‌شده"}}
    )
    body = page(client, buyer, order)
    positions = [
        body.index(marker)
        for marker in ('id="order-next"', 'id="order-purchase"', 'class="order-records"')
    ]
    assert positions == sorted(positions)
    records = body.split('class="order-records"', 1)[1]
    for disclosure in ("order-help", "order-conditions", "order-tracking", "order-history"):
        assert f'<details class="order-record" id="{disclosure}">' in records
    assert reverse("order-tracking", args=[order.tracking_token]) in body
    assert reverse("order-invoice", args=[order.order_number]) in body
    assert order.support_identifier.grouped_token in body
    assert "متن ثبت‌شده" in body
    assert body.count("data-order-total") == 1


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


def test_one_refund_is_stated_by_the_card_and_the_totals_not_again_on_its_line(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "mixed_refund")
    body = page(client, buyer, order)
    purchase = body.split('id="order-purchase"', 1)[1].split('class="order-records"', 1)[0]
    assert "data-line-money" not in purchase
    assert 'data-money="owed"' in purchase and "به شما برمی‌گردانیم" in card_of(body)

import re
from datetime import timedelta
from decimal import Decimal
from html import unescape
from math import ceil

import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product
from apps.core import support
from apps.core.formatting import format_jalali, format_toman, to_persian_digits
from apps.orders import selectors, services, telegram_panel
from apps.orders.models import Order, OrderItem, OrderSupportIdentifier
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
    ("paused", "در حال آماده‌سازی", "progress", "در حال آماده‌سازی است", False),
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
    elif state == "paused":
        now = timezone.now()
        pause(items[0], due=now - timedelta(minutes=5), at=now - timedelta(hours=1))
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


def pause(item, *, due, at):
    """A queued item whose running delivery clock stopped at `at`, frozen on `due`."""
    assert OrderItem.objects.get(pk=item.pk).due_at is not None
    OrderItem.objects.filter(pk=item.pk).update(due_at=due, sla_paused_at=at)


def when(moment):
    """A deadline as the order page prints it."""
    local = timezone.localtime(moment)
    return f"{format_jalali(local)}، ساعت {to_persian_digits(local.strftime('%H:%M'))}"


def purchase_of(body):
    return body.split('id="order-purchase"', 1)[1].split('class="order-records"', 1)[0]


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


@pytest.mark.parametrize("state", [case[0] for case in CASES if case[-1]])
def test_a_card_asks_once_for_what_to_send_in_telegram(client, buyer, plan, operator, state):
    card = card_of(page(client, buyer, order_for_state(buyer, plan, operator, state)))
    # The button and the safety line name Telegram but ask for nothing.
    said = re.sub(
        r'<a class="btn[^"]*"[^>]*>.*?</a>|<p class="small">.*?</p>', "", card, flags=re.S
    )
    sentences = re.findall(r"[^.؟]+[.؟]", text(said))
    asks = [one for one in sentences if "تلگرام" in one and re.search("بفرستید|پیام بدهید", one)]
    assert len(asks) == 1, asks
    if state in ("owed", "mixed_refund"):
        # Two numbers are asked for, so the promise names the one it counts from.
        assert "حداکثر یک روز کاری پس از دریافت آن به حسابتان واریز شود" in text(said)


def test_the_copied_line_carries_the_order_number_and_the_panel_finds_its_identifier(
    client, buyer, plan, operator
):
    order = services.place_order(user=buyer, lines=[services.OrderLine(plan, 1)])
    # Every digit here is also in the identifier's alphabet, so only the line's words keep
    # the number from running into the identifier.
    Order.objects.filter(pk=order.pk).update(order_number=23456789)
    order.refresh_from_db()
    grouped = OrderSupportIdentifier.objects.get(order_id=order.pk).grouped_token
    copied = unescape(re.search(r'data-copy-text="([^"]+)"', page(client, buyer, order))[1])
    assert copied == f"سفارش ۲۳۴۵۶۷۸۹، شناسه‌ی پشتیبانی {grouped}"
    match = services.match_support_token
    assert match(order_id=order.pk, actor=operator, support_token=copied)
    assert match(order_id=order.pk, actor=operator, support_token=grouped)
    last = "3" if grouped[-1] == "2" else "2"
    wrong = copied[:-1] + last
    assert not match(order_id=order.pk, actor=operator, support_token=wrong)
    assert not match(order_id=order.pk, actor=operator, support_token=copied + " " * 30)
    other = services.place_order(
        user=User.objects.create_user("other@example.test"),
        lines=[services.OrderLine(plan, 1)],
    )
    assert not match(order_id=other.pk, actor=operator, support_token=copied)


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
    assert 'name="customer_input"' not in body
    for form in re.findall(r"<form\b.*?</form>", body, re.S):
        if "expected_event_id" in form:
            assert re.search(r'action="/account/orders/\d+/items/\d+/claim/"', form)


@pytest.mark.parametrize("state,pill,badge,fragment,contact", CASES)
def test_page_headings_match_status_delivery_and_purchase_sections(
    client, buyer, plan, operator, state, pill, badge, fragment, contact
):
    order = order_for_state(buyer, plan, operator, state)
    body = page(client, buyer, order)
    assert headings(body, 1) == [f"سفارش {to_persian_digits(order.order_number)}"]
    titles = headings(body, 2)
    assert fragment in titles[0]
    delivered = any(item.delivered_at for item in order.items.all())
    delivery, review = (["اطلاعات تحویل"], ["نظر شما"]) if delivered else ([], [])
    assert titles[1:] == [*delivery, "محصولات و مبلغ سفارش", *review]


def test_a_cancelled_unpaid_order_says_no_payment_was_recorded_and_offers_no_support(
    client, buyer, plan, operator
):
    card = card_of(page(client, buyer, order_for_state(buyer, plan, operator, "cancelled")))
    assert "پرداختی برای این سفارش ثبت نشده بود" in card
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
    assert figure in card and "خرید سرویس نمونه لغو شد، چون نتوانستیم آن را تهیه کنیم." in text(
        card
    )
    assert "شماره‌ی کارت یا شبای به نام خودتان" in card and "حداکثر یک روز کاری" in card
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
    assert "لغو این سفارش شامل بازگشت وجه نمی‌شود" in card
    assert "پرداختی برای آن ثبت نشده بود" not in card and "رایگان" not in card


def test_a_queued_item_past_its_delivery_time_reads_as_late_and_points_to_support(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "queued")
    order.items.update(due_at=timezone.now() - timedelta(minutes=5))
    card = card_of(page(client, buyer, order))
    assert '<span class="badge badge--danger"' in card and "تأخیر در تحویل" in card
    assert "گذشت" in card and support.details()["telegram_url"] in card


def test_a_paused_item_reads_in_preparation_on_every_surface(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "paused")
    ready, late = "در حال آماده‌سازی", "تأخیر در تحویل"
    card = card_of(page(client, buyer, order))
    badge = re.search(r'<span class="badge[^"]*"[^>]*>(.*?)</span>', card, re.S)
    assert badge and text(badge[1]) == ready and late not in card
    listed = client.get(reverse("order-list")).content.decode()
    header = re.search(r'<header class="order-heading">(.*?)</header>', listed, re.S)
    assert header and ready in text(header[1]) and late not in listed
    account = client.get(reverse("account")).content.decode()
    current = account.split('id="current-order-title"', 1)[1].split("</section>", 1)[0]
    assert ready in current and late not in current
    screen, _ = telegram_panel.render(buyer, f"o:{order.order_number}")
    assert f"وضعیت</th><td><b>{ready}</b>" in screen and "زمان تحویل گذشته است" not in screen
    invoice = client.get(reverse("order-invoice", args=[order.order_number]))
    assert invoice.context["invoice_data"]["status"] == ready


@pytest.mark.parametrize("minutes", [-5, 90])
def test_a_paused_item_shows_no_frozen_date_apology_or_hours(
    client, buyer, plan, operator, minutes
):
    now = timezone.now().replace(second=0, microsecond=0)
    with time_machine.travel(now, tick=False):
        order, items = paid_order(buyer, operator, [services.OrderLine(plan, 1)])
        frozen = now + timedelta(minutes=minutes)
        pause(items[0], due=frozen, at=now - timedelta(hours=1))
        card = text(card_of(page(client, buyer, order)))
    for false in ("گذشت", "تحویل می‌دهیم", "پس از تأیید پرداخت", when(frozen)):
        assert false not in card, false
    assert "سرویس نمونه در حال آماده‌سازی است" in card
    assert support.details()["telegram_url"] not in card_of(page(client, buyer, order))


def test_a_paused_unit_and_running_units_of_one_plan_each_keep_their_own_line(
    client, buyer, plan, operator
):
    now = timezone.now().replace(second=0, microsecond=0)
    with time_machine.travel(now, tick=False):
        order, items = paid_order(buyer, operator, [services.OrderLine(plan, 3)])
        frozen, second, third = (now + timedelta(hours=hours) for hours in (10, 20, 30))
        pause(items[0], due=frozen, at=now - timedelta(hours=1))
        OrderItem.objects.filter(pk=items[1].pk).update(due_at=second)
        OrderItem.objects.filter(pk=items[2].pk).update(due_at=third)
        body = page(client, buyer, order)
        listed = client.get(reverse("order-list")).content.decode()
        screen, _ = telegram_panel.render(buyer, f"o:{order.order_number}")
        invoice = client.get(reverse("order-invoice", args=[order.order_number]))
    card = text(card_of(body))
    assert "تحویل می‌دهیم" not in card and "در حال آماده‌سازی است" in card
    purchase = text(purchase_of(body))
    assert purchase_of(body).count('class="order-line__facts"') == 3
    assert purchase.count("تحویل تا") == 2
    assert when(second) in purchase and when(third) in purchase and when(frozen) not in purchase
    # Where no line prints a date, the three units stay one row.
    assert listed.count('class="order-line__facts"') == 1 and "تعداد: ۳" in listed
    assert screen.count("محصول</th>") == 1 and "۳ عدد" in screen
    lines = invoice.context["invoice_data"]["lines"]
    assert len(lines) == 1 and lines[0]["quantity"] == "۳"
    assert body.count('class="order-terms__item"') == 1


def test_an_input_wait_beside_paused_and_running_units_keeps_its_hours_and_their_dates(
    client, buyer, plan, operator
):
    now = timezone.now().replace(second=0, microsecond=0)
    with time_machine.travel(now, tick=False):
        lines = [services.OrderLine(plan, 2), services.OrderLine(second_plan(), 1)]
        order, items = paid_order(buyer, operator, lines)
        held, running = [item for item in items if item.plan_id == plan.pk]
        waiting = next(item for item in items if item.plan_id != plan.pk)
        act(services.request_input, waiting, operator)
        waiting.refresh_from_db()
        assert waiting.sla_paused_at is not None and waiting.due_at is not None
        frozen, due = now + timedelta(hours=30), now + timedelta(hours=20)
        pause(held, due=frozen, at=now - timedelta(hours=1))
        OrderItem.objects.filter(pk=running.pk).update(due_at=due)
        body = page(client, buyer, order)
    left = ceil((waiting.due_at - waiting.sla_paused_at).total_seconds() / 3600)
    card = text(card_of(body))
    assert "اطلاعات فعال‌سازی سرویس دوم لازم است" in card
    assert (
        "پس از رسیدن اطلاعات فعال‌سازی، خریدتان را حداکثر تا"
        f" {to_persian_digits(max(1, left))} ساعت بعد تحویل می‌دهیم"
    ) in card
    purchase = purchase_of(body)
    rows = [
        text(row)
        for row in re.findall(r'class="order-line__facts">(.*?)</div>', purchase, re.S)
        if "سرویس نمونه" in row
    ]
    assert len(rows) == 2, rows
    assert purchase.count("تحویل تا") == 1
    assert when(due) in text(purchase) and when(frozen) not in text(purchase)


def test_a_late_item_beside_a_paused_one_apologises_only_for_its_own_time(
    client, buyer, plan, operator
):
    now = timezone.now().replace(second=0, microsecond=0)
    with time_machine.travel(now, tick=False):
        items = two_products(buyer, plan, operator)
        overdue, frozen = now - timedelta(minutes=5), now - timedelta(hours=3)
        OrderItem.objects.filter(pk=items[0].pk).update(due_at=overdue)
        pause(items[1], due=frozen, at=now - timedelta(hours=4))
        body = page(client, buyer, items[0].order)
    card = card_of(body)
    said = text(card)
    assert '<span class="badge badge--danger"' in card and "تأخیر در تحویل" in said
    assert f"قرار بود سرویس نمونه تا {when(overdue)} تحویل شود" in said
    assert "سرویس دوم" not in said and when(frozen) not in said
    # The card already names the late time, so its line does not repeat it.
    assert "قرار بود تا" not in text(purchase_of(body))


def late_pair(buyer, plan, operator, first, second):
    """Two late products, each promised at its own time."""
    items = two_products(buyer, plan, operator)
    for item, due in zip(items, (first, second), strict=True):
        OrderItem.objects.filter(pk=item.pk).update(due_at=due)
    return items[0].order


def test_late_items_promised_at_different_times_each_keep_their_own_time(
    client, buyer, plan, operator
):
    now = timezone.now().replace(second=0, microsecond=0)
    with time_machine.travel(now, tick=False):
        earlier, later = now - timedelta(hours=3), now - timedelta(minutes=5)
        body = page(client, buyer, late_pair(buyer, plan, operator, earlier, later))
    said = text(card_of(body))
    assert "تأخیر در تحویل" in said and "بابت تأخیر در تحویل سرویس نمونه، سرویس دوم" in said
    assert (
        "زمان تحویلی را که برای هر خرید گفته بودیم، در بخش «محصولات و مبلغ سفارش» می‌بینید." in said
    )
    # One date would promise the later product the earlier time.
    assert when(earlier) not in said and when(later) not in said
    purchase = text(purchase_of(body))
    for due in (earlier, later):
        assert f"قرار بود تا {when(due)} تحویل شود" in purchase


def test_late_units_of_one_plan_promised_at_different_times_get_a_line_each(
    client, buyer, plan, operator
):
    now = timezone.now().replace(second=0, microsecond=0)
    with time_machine.travel(now, tick=False):
        order, items = paid_order(buyer, operator, [services.OrderLine(plan, 2)])
        earlier, later = now - timedelta(hours=3), now - timedelta(minutes=5)
        for item, due in zip(items, (earlier, later), strict=True):
            OrderItem.objects.filter(pk=item.pk).update(due_at=due)
        body = page(client, buyer, order)
    said = text(card_of(body))
    assert when(earlier) not in said
    # One product name, two purchases: the sentence must not say "each of them" of one name.
    assert (
        "بابت تأخیر در تحویل سرویس نمونه عذرخواهی می‌کنیم و زمان تحویلی را که برای هر خرید" in said
    )
    purchase = purchase_of(body)
    assert purchase.count('class="order-line__facts"') == 2
    for due in (earlier, later):
        assert f"قرار بود تا {when(due)} تحویل شود" in text(purchase)


def test_late_items_promised_at_the_same_time_keep_the_dated_apology(client, buyer, plan, operator):
    now = timezone.now().replace(second=0, microsecond=0)
    with time_machine.travel(now, tick=False):
        due = now - timedelta(minutes=5)
        # Seconds apart, one printed minute: still one time.
        order = late_pair(buyer, plan, operator, due, due + timedelta(seconds=30))
        body = page(client, buyer, order)
    said = text(card_of(body))
    assert f"قرار بود سرویس نمونه، سرویس دوم تا {when(due)} تحویل شود و بابت این تأخیر" in said
    assert "زمان تحویلی را که" not in said


def test_a_late_line_keeps_its_time_when_the_order_leads_with_something_else(
    client, buyer, plan, operator
):
    now = timezone.now().replace(second=0, microsecond=0)
    with time_machine.travel(now, tick=False):
        items = two_products(buyer, plan, operator)
        overdue = now - timedelta(minutes=5)
        OrderItem.objects.filter(pk=items[0].pk).update(due_at=overdue)
        act(services.request_input, items[1], operator)
        body = page(client, buyer, items[0].order)
    assert "در انتظار اطلاعات شما" in text(card_of(body)) and when(overdue) not in text(
        card_of(body)
    )
    assert f"قرار بود تا {when(overdue)} تحویل شود" in text(purchase_of(body))


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
    first = timezone.now() - timedelta(days=10)
    order.items.update(
        delivered_at=first, warranty_ends_on=timezone.localdate(first) + timedelta(days=7)
    )
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
    assert "برای اطلاع از دلیل رد شدن درخواست تعویض، از پشتیبانی بپرسید." in card


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


@pytest.mark.parametrize("state", ["mixed_refund", "mixed", "owed", "queued", "paused"])
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
    assert f"وضعیت</th><td><b>{label}</b>" in screen


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

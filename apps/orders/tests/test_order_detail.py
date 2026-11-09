import pytest
from django.urls import reverse
from django.utils import timezone

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
    ("overdue", "مهلت پرداخت تمام شده است.", False),
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
        assert "برای پیگیری پرداخت یا بازپرداخت، با پشتیبانی تماس بگیرید." in body
    assert 'name="customer_input"' not in body and "expected_event_id" not in body


def test_order_detail_keeps_purchase_help_and_quiet_records_in_reading_order(
    client, buyer, plan, operator
):
    order = order_for_state(buyer, plan, operator, "unpaid")
    Order.objects.filter(pk=order.pk).update(
        policy_snapshot={"terms": {"title": "شرایط زمان خرید", "body": "متن ثبت‌شده"}}
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
    assert '<details class="order-record" id="order-policies">' in body
    assert '<details class="order-record" id="order-history">' in body
    assert '<details class="order-record" id="order-tracking">' in body
    assert reverse("order-tracking", args=[order.tracking_token]) in body
    assert reverse("order-invoice", args=[order.order_number]) in body
    assert order.support_identifier.grouped_token in body
    assert "متن ثبت‌شده" in body
    assert body.count("data-order-total") == 1

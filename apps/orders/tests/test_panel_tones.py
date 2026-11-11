from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import Notification
from apps.orders import status as order_status
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_order_detail import order_for_state

pytestmark = pytest.mark.django_db(transaction=True)


def queue(client, operator):
    client.force_login(operator)
    url = reverse("admin:orders_order_changelist")
    return client.get(url, {"tab": "all"}).content.decode()


@pytest.mark.parametrize(
    "state, tone, line",
    [
        ("queued", "progress", "تحویل بدهید"),
        ("unpaid", "attention", "منتظر پرداخت مشتری"),
        ("waiting", "attention", "منتظر اطلاعات مشتری"),
        ("claimed", "progress", "درخواست تعویض را بررسی کنید"),
        ("owed", "attention", "بازگشت وجه ۲۰۰,۰۰۰ تومان را واریز کنید"),
        ("delivered", "done", None),
        ("cancelled", "closed", None),
    ],
)
def test_the_queue_gives_each_item_its_tone_and_each_order_its_next_step(
    client, buyer, plan, operator, state, tone, line
):
    order_for_state(buyer, plan, operator, state)
    body = queue(client, operator)
    assert f'class="order-status order-queue-state" data-tone="{tone}"' in body
    if line:
        assert line in body
    else:
        assert 'class="order-next-line"' not in body


def test_a_late_item_is_a_problem_and_the_first_thing_to_do(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "queued")
    order.items.update(due_at=timezone.now() - timedelta(minutes=5))
    body = queue(client, operator)
    assert 'data-tone="problem"' in body and "دیر شده: تحویل بدهید" in body


def test_the_order_page_shows_each_items_status_in_its_tone(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "owed")
    client.force_login(operator)
    body = client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
    assert '<span class="order-status" data-tone="attention">' in body
    assert "بازگشت وجه ۲۰۰,۰۰۰ تومان را واریز کنید" in body


def test_unfold_labels_draw_the_shared_pill_in_its_tone(client, plan, operator):
    client.force_login(operator)
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()
    assert '<span class="panel-pill" data-tone="done">' in body
    assert "text-[11px] uppercase" not in body


@pytest.mark.parametrize(
    "value, tone", [("pending", "progress"), ("sent", "done"), ("failed", "problem")]
)
def test_each_order_message_status_reads_in_its_tone(client, buyer, plan, operator, value, tone):
    order_for_state(buyer, plan, operator, "queued")  # confirming payment queues its notice
    assert Notification.objects.exists()
    Notification.objects.update(status=value)
    client.force_login(operator)
    body = client.get(reverse("admin:notifications_notification_changelist")).content.decode()
    assert f'<span class="panel-pill" data-tone="{tone}">' in body


@pytest.mark.parametrize(
    "states, first",
    [
        (["unpaid", "queued"], "queued"),
        (["awaiting_input", "late"], "late"),
        (["refund_owed", "replacement"], "replacement"),
        (["awaiting_input", "refund_owed"], "refund_owed"),
        (["delivered", "cancelled"], None),
    ],
)
def test_the_operators_next_step_puts_their_own_moves_first(states, first):
    found = order_status.operator_next(states)
    assert (found["state"] if found else None) == first

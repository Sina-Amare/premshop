from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.email_log import fingerprint
from apps.core.models import MailAttempt
from apps.notifications.models import Notification
from apps.orders import status as order_status
from apps.orders.models import OrderItem
from apps.orders.services import OrderLine
from apps.orders.tests.test_delivery import deliver
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_order_detail import order_for_state
from apps.orders.tests.test_paid_cancellation import paid_order
from apps.orders.tests.test_replacement import claim

pytestmark = pytest.mark.django_db(transaction=True)


def queue(client, operator, tab="all"):
    client.force_login(operator)
    url = reverse("admin:orders_order_changelist")
    return client.get(url, {"tab": tab}).content.decode()


def next_line(tone, words):
    return f'<p class="order-next-line" data-tone="{tone}">{words}</p>'


@pytest.mark.parametrize(
    "state, tone, line",
    [
        ("queued", "progress", "تحویل بدهید"),
        ("unpaid", "attention", "منتظر پرداخت مشتری"),
        ("waiting", "attention", "منتظر اطلاعات مشتری"),
        ("claimed", "progress", "درخواست تعویض را بررسی کنید"),
        ("owed", "attention", "بازگشت وجه ۲۰۰,۰۰۰ تومان را واریز کنید"),
        ("delivered", "done", None),
        ("cancelled", "problem", None),
        ("overdue", "closed", None),
    ],
)
def test_the_queue_gives_each_item_its_tone_and_each_order_its_next_step(
    client, buyer, plan, operator, state, tone, line
):
    order = order_for_state(buyer, plan, operator, state)
    item = order.items.get()
    body = queue(client, operator)
    pill = f'data-tone="{tone}" data-state="{item.status}">{item.get_status_display()}</span>'
    assert f'class="order-status order-queue-state" {pill}' in body
    if line:
        assert next_line(tone, line) in body
    else:
        assert 'class="order-next-line"' not in body


def test_a_late_item_is_a_problem_and_the_first_thing_to_do(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "queued")
    order.items.update(due_at=timezone.now() - timedelta(minutes=5))
    body = queue(client, operator)
    assert next_line("problem", "دیر شده: تحویل بدهید") in body


def test_a_tab_shows_its_own_items_but_the_next_step_weighs_them_all(client, buyer, plan, operator):
    order_for_state(buyer, plan, operator, "mixed")  # one unit awaits details, one is queued
    body = queue(client, operator, "waiting")
    assert f'data-state="{OrderItem.Status.QUEUED.value}"' not in body
    assert next_line("progress", "تحویل بدهید") in body


def test_the_claims_tab_still_puts_a_late_delivery_first(client, buyer, plan, operator):
    _, items = paid_order(buyer, operator, [OrderLine(plan, 2)])
    deliver(items[0], operator)
    claim(items[0], operator)
    OrderItem.objects.filter(pk=items[1].pk).update(due_at=timezone.now() - timedelta(minutes=5))
    body = queue(client, operator, "claims")
    assert f'data-state="{OrderItem.Status.QUEUED.value}"' not in body
    assert next_line("problem", "دیر شده: تحویل بدهید") in body


def test_the_order_page_shows_each_items_status_in_its_tone(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "owed")
    client.force_login(operator)
    body = client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
    assert '<span class="order-status" data-tone="attention">' in body
    assert next_line("attention", "بازگشت وجه ۲۰۰,۰۰۰ تومان را واریز کنید") in body
    # The payment's history reads in its tone, and each move in the order's history carries
    # the tone of the status it reached.
    assert '<span class="order-status" data-tone="done">تأییدشده</span>' in body
    # A cancellation is red with its cross, here as on the customer's page (owner, 2026-11-11).
    assert '<span class="order-status" data-tone="problem" data-state="CANCELLED">' in body


def test_a_sent_refund_reads_as_closed_in_the_orders_history(client, buyer, plan, operator):
    order = order_for_state(buyer, plan, operator, "refunded")
    client.force_login(operator)
    body = client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
    assert '<span class="order-status" data-tone="closed">واریز شد</span>' in body


def test_unfold_labels_draw_the_shared_pill_in_its_tone(client, plan, operator):
    client.force_login(operator)
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()
    assert '<span class="panel-pill" data-tone="done">' in body
    assert "text-[11px] uppercase" not in body


@pytest.mark.parametrize(
    "value, tone",
    [
        (Notification.Status.PENDING, "progress"),
        (Notification.Status.SENT, "done"),
        (Notification.Status.FAILED, "problem"),
    ],
)
def test_each_order_message_status_reads_in_its_tone(client, buyer, plan, operator, value, tone):
    order_for_state(buyer, plan, operator, "queued")  # confirming payment queues its notice
    assert Notification.objects.exists()
    Notification.objects.update(status=value)
    client.force_login(operator)
    body = client.get(reverse("admin:notifications_notification_changelist")).content.decode()
    assert f'<span class="panel-pill" data-tone="{tone}">' in body


@pytest.mark.parametrize(
    "value, tone",
    [
        (MailAttempt.Status.IN_FLIGHT, "progress"),
        (MailAttempt.Status.ACCEPTED, "done"),
        (MailAttempt.Status.REJECTED, "problem"),
        (MailAttempt.Status.UNCERTAIN, "problem"),
        (MailAttempt.Status.BLOCKED, "problem"),
    ],
)
def test_each_mail_attempt_reads_in_its_tone(client, operator, value, tone):
    """Amber is the customer's move or a clock: a send nobody can confirm is a problem."""
    MailAttempt.objects.create(
        template="password_reset", recipient_hash=fingerprint("buyer@example.test"), status=value
    )
    client.force_login(operator)
    body = client.get(reverse("admin:core_mailattempt_changelist")).content.decode()
    assert f'<span class="panel-pill" data-tone="{tone}">{value.label}</span>' in body


@pytest.mark.parametrize(
    "states, first",
    [
        (["unpaid", "queued"], "queued"),
        (["awaiting_input", "late"], "late"),
        (["queued", "late"], "late"),
        (["replacement", "queued"], "replacement"),
        (["refund_owed", "replacement"], "replacement"),
        (["awaiting_input", "refund_owed"], "refund_owed"),
        (["delivered", "cancelled"], None),
        (["expired", "cancelled"], None),
    ],
)
def test_the_operators_next_step_puts_their_own_moves_first(states, first):
    assert order_status.operator_next(states) == first

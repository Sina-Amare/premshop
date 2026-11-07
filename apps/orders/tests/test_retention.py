from datetime import UTC, date, datetime, timedelta

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.orders import models, services, tasks
from apps.orders.clock import TEHRAN
from apps.orders.tests.test_delivery import deliver, queued
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_replacement import claim, replace

pytestmark = pytest.mark.django_db(transaction=True)

INPUT = "customer-login@example.test"
# 01:00 in Tehran on 1 December is still 30 November in UTC, which is how the database returns it.
DELIVERED_AT = datetime(2026, 12, 1, 1, 0, tzinfo=TEHRAN).astimezone(UTC)


@pytest.mark.parametrize(
    ("warranty", "expires_at", "deadline"),
    [
        ("none", date(2027, 1, 1), date(2027, 1, 31)),
        ("none", None, date(2026, 12, 31)),
        ("days_7", date(2027, 1, 1), date(2027, 1, 31)),
        ("days_7", date(2026, 12, 3), date(2027, 1, 7)),
        ("days_7", None, date(2027, 1, 7)),
        ("full_period", date(2027, 1, 1), date(2027, 1, 31)),
        (None, None, date(2026, 12, 31)),
    ],
)
def test_details_are_kept_until_the_later_of_expiry_and_warranty_then_thirty_days(
    warranty, expires_at, deadline
):
    assert (
        services.retention_deadline(
            delivered_at=DELIVERED_AT, expires_at=expires_at, warranty=warranty
        )
        == deadline
    )


def delivered_item(buyer, plan, operator, *, days_ago, expires_at=None):
    """An item whose retention deadline is days_ago - 30 days before today (no warranty)."""
    plan.product.warranty = "none"
    plan.product.save()
    _, item = queued(buyer, plan, operator)
    models.OrderItem.objects.filter(pk=item.pk).update(customer_input=INPUT)
    deliver(item, operator)
    models.OrderItem.objects.filter(pk=item.pk).update(
        delivered_at=timezone.now() - timedelta(days=days_ago), expires_at=expires_at
    )
    item.refresh_from_db()
    return item


def raw(item):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM orders_deliveryfield f JOIN orders_delivery d"
            " ON f.delivery_id = d.id WHERE d.order_item_id = %s",
            [item.pk],
        )
        (fields,) = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM orders_delivery WHERE order_item_id = %s", [item.pk])
        (sets,) = cursor.fetchone()
        cursor.execute("SELECT customer_input FROM orders_orderitem WHERE id = %s", [item.pk])
        (customer_input,) = cursor.fetchone()
    return sets, fields, customer_input


def test_a_due_item_loses_every_set_its_note_and_its_input(buyer, plan, operator):
    item = delivered_item(buyer, plan, operator, days_ago=31)
    claim(item, operator)
    replace(item, operator)
    item.refresh_from_db()
    assert raw(item)[0] == 2
    before = item.events.count()
    assert services.delete_expired_details() == 1
    item.refresh_from_db()
    assert raw(item) == (0, 0, "")
    event = item.events.latest("pk")
    assert item.status == "DELIVERED" and item.events.count() == before + 1
    assert (event.from_status, event.to_status, event.actor, event.actor_user_id) == (
        "DELIVERED",
        "DELIVERED",
        "system",
        None,
    )
    assert "اطلاعات تحویل" in event.note and "اطلاعات مشتری" in event.note
    assert (
        services.reveal_delivery(order_id=item.order_id, item_id=item.pk, actor=operator, ip=None)
        is None
    )


def test_a_second_run_changes_nothing(buyer, plan, operator):
    item = delivered_item(buyer, plan, operator, days_ago=40)
    assert services.delete_expired_details() == 1
    before = models.OrderItemEvent.objects.count()
    assert services.delete_expired_details() == 0
    assert models.OrderItemEvent.objects.count() == before
    assert item.events.filter(actor="system").count() == 1


def test_a_set_due_three_days_ago_is_deleted_on_the_next_run(buyer, plan, operator):
    item = delivered_item(buyer, plan, operator, days_ago=33)
    assert services.delete_expired_details() == 1
    assert raw(item) == (0, 0, "")


def test_a_set_is_deleted_on_its_deadline_day_and_not_the_day_before(buyer, plan, operator):
    item = delivered_item(buyer, plan, operator, days_ago=10)
    deadline = timezone.localdate(item.delivered_at) + timedelta(days=30)
    assert services.delete_expired_details(today=deadline - timedelta(days=1)) == 0
    assert raw(item)[0] == 1
    assert services.delete_expired_details(today=deadline) == 1
    assert raw(item) == (0, 0, "")


def test_an_item_not_yet_due_and_an_open_claim_are_untouched(buyer, plan, operator):
    fresh = delivered_item(buyer, plan, operator, days_ago=1)
    open_claim = delivered_item(buyer, plan, operator, days_ago=60)
    claim(open_claim, operator)
    future = delivered_item(
        buyer, plan, operator, days_ago=60, expires_at=timezone.localdate() + timedelta(days=5)
    )
    assert services.delete_expired_details() == 0
    for item in (fresh, open_claim, future):
        sets, fields, customer_input = raw(item)
        assert (sets, fields) == (1, 1) and customer_input != ""


def test_a_delivered_item_cancelled_later_is_still_covered(buyer, plan, operator):
    item = delivered_item(buyer, plan, operator, days_ago=31)
    models.OrderItem.objects.filter(pk=item.pk).update(
        status="CANCELLED", cancel_reason="warranty_refund"
    )
    assert services.delete_expired_details() == 1
    assert raw(item) == (0, 0, "")
    assert item.events.latest("pk").to_status == "CANCELLED"


def test_a_cancelled_undelivered_items_input_goes_thirty_days_after_cancellation(
    buyer, plan, operator
):
    order = services.place_order(user=buyer, lines=[services.OrderLine(plan, 1, None)])
    item = order.items.get()
    models.OrderItem.objects.filter(pk=item.pk).update(customer_input=INPUT)
    services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    cancelled = timezone.localdate(item.events.get(to_status="CANCELLED").created_at)
    assert services.delete_expired_details(today=cancelled + timedelta(days=29)) == 0
    assert raw(item)[2] != ""
    assert services.delete_expired_details(today=cancelled + timedelta(days=30)) == 1
    assert raw(item) == (0, 0, "")
    event = item.events.latest("pk")
    assert (event.from_status, event.to_status, event.actor) == ("CANCELLED", "CANCELLED", "system")
    assert "اطلاعات مشتری" in event.note and "اطلاعات تحویل" not in event.note
    assert services.delete_expired_details(today=cancelled + timedelta(days=31)) == 0


def test_an_unpaid_or_queued_items_input_is_kept(buyer, plan, operator):
    order = services.place_order(user=buyer, lines=[services.OrderLine(plan, 1, None)])
    other = User.objects.create_user("other@example.test")
    _, queued_item = queued(other, plan, operator)
    models.OrderItem.objects.update(customer_input=INPUT)
    assert services.delete_expired_details(today=timezone.localdate() + timedelta(days=400)) == 0
    for item in (order.items.get(), queued_item):
        assert raw(item)[2] != ""


def test_the_reveal_page_says_the_details_were_deleted(client, buyer, plan, operator):
    item = delivered_item(buyer, plan, operator, days_ago=31)
    services.delete_expired_details()
    client.force_login(operator)
    url = reverse("admin:orders_order_reveal_delivery", args=[item.order_id, item.pk])
    body = client.post(url).content.decode()
    assert "طبق مهلت نگهداری حذف شده است" in body
    assert not models.CredentialAccessLog.objects.exists()


def test_the_nightly_task_runs_the_job(buyer, plan, operator):
    item = delivered_item(buyer, plan, operator, days_ago=31)
    assert tasks.clean_delivered_details.call_local() == 1
    assert raw(item) == (0, 0, "")

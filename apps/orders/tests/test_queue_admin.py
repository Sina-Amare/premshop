from datetime import timedelta

import pytest
import time_machine
from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.formatting import to_persian_digits
from apps.orders.models import Order, OrderItem
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(client):
    user = User.objects.create_superuser("queue-operator@example.test")
    client.force_login(user)
    return user


def listing():
    return reverse("admin:orders_order_changelist")


def make_order(buyer, plan, status="QUEUED", **times):
    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    OrderItem.objects.filter(order=order).update(status=status, **times)
    return order


def test_tabs_counts_urgency_search_and_mixed_orders(client, operator, buyer, plan):
    now = timezone.now()
    later = make_order(buyer, plan, due_at=now + timedelta(hours=5))
    overdue = make_order(buyer, plan, due_at=now - timedelta(hours=1))
    waiting = make_order(buyer, plan, "AWAITING_INPUT", input_requested_at=now)
    item = waiting.items.get()
    item.pk = None
    item.status, item.due_at = "QUEUED", now + timedelta(hours=2)
    item.save()
    unpaid = make_order(buyer, plan, "PENDING_PAYMENT")
    response = client.get(listing())
    assert list(response.context["cl"].result_list) == [overdue, waiting, later]
    assert response.context["queue_counts"] == {"unpaid": 1, "queued": 3, "waiting": 1}
    for tab, expected in [("unpaid", [unpaid]), ("waiting", [waiting])]:
        response = client.get(listing(), {"tab": tab})
        assert list(response.context["cl"].result_list) == expected
    for term in [str(overdue.order_number), to_persian_digits(overdue.order_number)]:
        response = client.get(listing(), {"q": term})
        assert list(response.context["cl"].result_list) == [overdue]
        assert response.context["queue_counts"] == {"unpaid": 0, "queued": 1, "waiting": 0}
    response = client.get(listing(), {"q": buyer.email.upper()})
    assert len(response.context["cl"].result_list) == 3
    assert client.get(listing(), {"q": "missing@example.test"}).context["cl"].result_count == 0
    assert client.get(listing(), {"tab": "all"}).context["cl"].result_count == 4


def test_waits_freeze_the_clock_without_inventing_a_deadline(client, operator, buyer, plan):
    now = timezone.now().replace(microsecond=0)
    first = make_order(buyer, plan, "AWAITING_INPUT", input_requested_at=now)
    paused = make_order(
        buyer,
        plan,
        "AWAITING_INPUT",
        input_requested_at=now - timedelta(days=1),
        sla_paused_at=now - timedelta(days=1),
        due_at=now - timedelta(days=1) + timedelta(hours=2),
    )
    with time_machine.travel(now, tick=False):
        body = client.get(listing(), {"tab": "waiting"}).content.decode()
        assert "در انتظار از" in body and "۰۲:۰۰:۰۰" in body and "متوقف" in body
        assert 'data-clock="overdue"' not in body
    with time_machine.travel(now + timedelta(days=2), tick=False):
        body = client.get(listing(), {"tab": "waiting"}).content.decode()
        assert "۰۲:۰۰:۰۰" in body and 'data-clock="overdue"' not in body
        detail = client.get(reverse("admin:orders_order_change", args=[paused.pk]))
        assert "۰۲:۰۰:۰۰" in detail.content.decode()
    assert first.items.get().due_at is None
    assert paused.items.get().due_at == now - timedelta(days=1) + timedelta(hours=2)


@pytest.mark.parametrize("tab", ["unpaid", "queued", "waiting", "all"])
def test_queue_is_private_and_refuses_before_order_queries(client, buyer, plan, tab):
    order = make_order(buyer, plan)
    # Short order numbers can also occur inside SVG path coordinates.
    order.order_number = 9876543210123456
    order.save(update_fields=["order_number"])
    for user in [None, buyer, User.objects.create_user("unpermitted@example.test", is_staff=True)]:
        client.logout()
        if user:
            client.force_login(user)
        with CaptureQueriesContext(connection) as queries:
            response = client.get(listing(), {"tab": tab})
        assert response.status_code == (403 if user and user.is_staff else 302)
        assert not any('"orders_' in query["sql"] for query in queries)
        for marker in (str(order.order_number), to_persian_digits(order.order_number)):
            assert marker not in response.content.decode()
        assert "queue_counts" not in (response.context or {})
        if response.status_code == 302:
            assert (
                "private" in response["Cache-Control"] and "no-store" in response["Cache-Control"]
            )
    staff = User.objects.create_user("view-orders@example.test", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_order"))
    client.force_login(staff)
    response = client.get(listing(), {"tab": tab})
    assert response.status_code == 200
    assert "private" in response["Cache-Control"] and "no-store" in response["Cache-Control"]


@pytest.mark.parametrize(
    "tab,status",
    [
        ("unpaid", "PENDING_PAYMENT"),
        ("queued", "QUEUED"),
        ("waiting", "AWAITING_INPUT"),
        ("all", "QUEUED"),
    ],
)
@pytest.mark.parametrize("search", [False, True])
def test_five_and_fifteen_orders_cost_the_same_queries(
    client, operator, buyer, plan, queries_for, tab, status, search
):
    def add(start, stop):
        for index in range(start, stop):
            customer = User.objects.create_user(f"queue-{index}@example.test")
            order = make_order(customer, plan, status, due_at=timezone.now())
            source = order.items.get()
            for _ in range(2):
                source.pk = None
                source.save()

    params = {"tab": tab, **({"q": "@example.test"} if search else {})}
    add(0, 5)
    few, response = queries_for(listing(), params)
    assert len(response.context["cl"].result_list) == 5
    add(5, 15)
    many, response = queries_for(listing(), params)
    assert len(response.context["cl"].result_list) == 15
    assert response.context["queue_counts"]["queued" if tab == "all" else tab] == 15
    assert few == many


def test_pagination_preserves_search_and_tab(client, operator, buyer, plan):
    for _ in range(26):
        make_order(buyer, plan)
    response = client.get(listing(), {"q": buyer.email, "tab": "queued"})
    assert len(response.context["cl"].result_list) == 25
    response = client.get(listing(), {"q": buyer.email, "tab": "queued", "p": 2})
    assert len(response.context["cl"].result_list) == 1
    assert response.context["queue_counts"]["queued"] == 26
    assert 'aria-label="صفحه‌های سفارش‌ها"' in response.content.decode()
    assert ">۲</span>" in response.content.decode()


def test_deadline_boundaries_and_missing_deadlines(client, operator, buyer, plan):
    now = timezone.now().replace(microsecond=0)
    with time_machine.travel(now, tick=False):
        make_order(buyer, plan, due_at=now)
        missing = make_order(buyer, plan)
        response = client.get(listing())
        body = response.content.decode()
        assert 'data-clock="overdue"' in body and "۰۰:۰۰:۰۰" in body
        assert "مهلت ثبت نشده است" in body
        assert list(response.context["cl"].result_list)[-1] == missing
        paused = make_order(
            buyer,
            plan,
            "AWAITING_INPUT",
            input_requested_at=now,
            sla_paused_at=now,
            due_at=now - timedelta(hours=1),
        )
        body = client.get(listing(), {"tab": "waiting"}).content.decode()
        assert "۰۱:۰۰:۰۰" in body and "گذشته از مهلت" in body
        assert 'data-clock="paused"' in body and 'data-clock="overdue"' not in body
        assert paused.items.get().due_at == now - timedelta(hours=1)


def test_unpaid_and_waiting_sort_oldest_first(client, operator, buyer, plan):
    now = timezone.now()
    newer = make_order(buyer, plan, "AWAITING_INPUT", input_requested_at=now)
    older = make_order(buyer, plan, "AWAITING_INPUT", input_requested_at=now - timedelta(days=7))
    assert list(client.get(listing(), {"tab": "waiting"}).context["cl"].result_list) == [
        older,
        newer,
    ]
    OrderItem.objects.filter(order__in=[newer, older]).update(status="PENDING_PAYMENT")
    Order.objects.filter(pk=newer.pk).update(unpaid_expires_at=now + timedelta(hours=1))
    Order.objects.filter(pk=older.pk).update(unpaid_expires_at=now - timedelta(hours=1))
    assert list(client.get(listing(), {"tab": "unpaid"}).context["cl"].result_list) == [
        older,
        newer,
    ]


def test_lists_never_select_or_render_customer_input(client, operator, buyer, plan, settings):
    order = make_order(buyer, plan)
    item = order.items.get()
    item.customer_input = "private-delivery-target@example.test"
    item.save(update_fields=["customer_input"])
    settings.FIELD_ENCRYPTION_KEY = ""
    for tab in ["queued", "waiting", "unpaid", "all"]:
        with CaptureQueriesContext(connection) as queries:
            response = client.get(listing(), {"tab": tab})
        assert response.status_code == 200
        assert "private-delivery-target" not in response.content.decode()
        assert not any('"customer_input"' in query["sql"] for query in queries)


def test_second_page_five_and_fifteen_rows_cost_the_same_queries(
    client, operator, buyer, plan, queries_for
):
    for _ in range(30):
        make_order(buyer, plan)
    params = {"tab": "queued", "q": buyer.email, "p": 2}
    few, response = queries_for(listing(), params)
    assert len(response.context["cl"].result_list) == 5
    for _ in range(10):
        make_order(buyer, plan)
    many, response = queries_for(listing(), params)
    assert len(response.context["cl"].result_list) == 15
    assert many == few

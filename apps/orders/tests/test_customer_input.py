from datetime import timedelta
from unittest.mock import patch

import pytest
import time_machine
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Plan
from apps.core.formatting import format_jalali
from apps.orders import services
from apps.orders.models import OrderItem, OrderItemEvent
from apps.orders.tests.test_input_clock import MONDAY, act, event_id, purchase
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = pytest.mark.django_db
OLD = "original@example.test"
NEW = "corrected@example.test"


def waiting(buyer, plan, operator):
    order, item = purchase(buyer, plan, operator, telegram=False, kind="email")
    OrderItem.objects.filter(pk=item.pk).update(customer_input=OLD)
    with time_machine.travel(MONDAY + timedelta(hours=4), tick=False):
        act(services.request_input, item, operator)
    item.refresh_from_db()
    return order, item


def input_url(order, item):
    return f"/account/orders/{order.order_number}/items/{item.pk}/input/"


def submit(item, actor, value=NEW, **changes):
    return act(services.submit_customer_input, item, actor, customer_input=value, **changes)


def test_submission_uses_frozen_policy_and_commits_encrypted_input_clock_and_event(
    buyer, plan, operator
):
    _, item = waiting(buyer, plan, operator)
    Plan.objects.filter(pk=plan.pk).update(input_type="none", requires_customer_input=False)
    initial_receipt = item.input_received_at
    with time_machine.travel(MONDAY + timedelta(days=1), tick=False):
        submit(item, buyer, "Corrected@EXAMPLE.TEST")
    item.refresh_from_db()
    assert (item.status, item.customer_input) == ("QUEUED", "Corrected@example.test")
    assert item.due_at == MONDAY + timedelta(days=1, hours=2)
    assert item.sla_paused_at is None and item.input_received_at == initial_receipt
    event = item.events.latest("pk")
    assert (event.actor, event.actor_user_id, event.note) == ("customer", buyer.pk, "")
    assert item.events.count() == 4
    with connection.cursor() as cursor:
        cursor.execute("SELECT customer_input FROM orders_orderitem WHERE id = %s", [item.pk])
        ciphertext = cursor.fetchone()[0]
    assert "Corrected" not in ciphertext and ciphertext


@pytest.mark.parametrize("value", ["", " ", "not-an-email", "x" * 2001, "a\x00b"])
def test_invalid_identifier_changes_neither_input_state_clock_nor_history(
    buyer, plan, operator, value
):
    _, item = waiting(buyer, plan, operator)
    before = OrderItem.objects.filter(pk=item.pk).values().get()
    with pytest.raises(ValidationError):
        submit(item, buyer, value)
    assert OrderItem.objects.filter(pk=item.pk).values().get() == before
    assert item.events.count() == 3


def test_username_policy_normalizes_a_customer_identifier(buyer, plan, operator):
    _, item = waiting(buyer, plan, operator)
    facts = {**item.product_snapshot, "input_type": "username"}
    OrderItem.objects.filter(pk=item.pk).update(product_snapshot=facts)
    submit(item, buyer, " @corrected_name ")
    item.refresh_from_db()
    assert item.customer_input == "corrected_name"


@pytest.mark.parametrize("policy", ["telegram", "none", "unreviewed", "missing"])
def test_customer_submission_never_confirms_telegram_or_unknown_input(
    buyer, plan, operator, policy
):
    _, item = waiting(buyer, plan, operator)
    facts = dict(item.product_snapshot)
    if policy == "telegram":
        facts["telegram_required"] = True
    elif policy == "missing":
        facts.pop("telegram_required")
    else:
        facts["input_type"] = policy
    OrderItem.objects.filter(pk=item.pk).update(product_snapshot=facts)
    with pytest.raises(ValidationError):
        submit(item, buyer)
    assert item.events.count() == 3


@pytest.mark.parametrize("kind", ["foreign", "inactive", "wrong_item", "unpaid", "not_paused"])
def test_service_rechecks_actor_pair_and_waiting_state(buyer, plan, operator, kind):
    order, item = waiting(buyer, plan, operator)
    actor = buyer
    error = ValidationError
    if kind == "foreign":
        actor = User.objects.create_user("foreign@example.test")
        error = PermissionDenied
    elif kind == "inactive":
        User.objects.filter(pk=buyer.pk).update(is_active=False)
        error = PermissionDenied
    elif kind == "wrong_item":
        order.pk += 99999
        error = PermissionDenied
    elif kind == "unpaid":
        OrderItem.objects.filter(pk=item.pk).update(paid_at=None)
    else:
        OrderItem.objects.filter(pk=item.pk).update(sla_paused_at=None)
    with pytest.raises(error):
        services.submit_customer_input(
            order_id=order.pk,
            item_id=item.pk,
            actor=actor,
            expected_event_id=event_id(item),
            customer_input=NEW,
        )
    assert item.events.count() == 3


def test_one_submission_per_episode_ignores_repeats_and_rejects_an_old_episode(
    buyer, plan, operator
):
    _, item = waiting(buyer, plan, operator)
    request = event_id(item)
    submit(item, buyer, expected_event_id=request)
    item.refresh_from_db()
    due = item.due_at
    submit(item, buyer, "different@example.test", expected_event_id=request)
    item.refresh_from_db()
    assert (item.customer_input, item.due_at, item.events.count()) == (NEW, due, 4)
    act(services.request_input, item, operator)
    with pytest.raises(ValidationError):
        submit(item, buyer, expected_event_id=request)
    submit(item, buyer, "second@example.test")
    item.refresh_from_db()
    assert item.customer_input == "second@example.test" and item.events.count() == 6


def test_event_failure_rolls_back_the_accepted_identifier_too(buyer, plan, operator):
    _, item = waiting(buyer, plan, operator)
    before = OrderItem.objects.filter(pk=item.pk).values().get()
    with patch.object(OrderItemEvent.objects, "create", side_effect=RuntimeError("event failed")):
        with pytest.raises(RuntimeError, match="event failed"):
            submit(item, buyer)
    assert OrderItem.objects.filter(pk=item.pk).values().get() == before
    assert item.events.count() == 3


@pytest.mark.parametrize("hour,minute,next_day", [(2, 0, False), (21, 30, True)])
def test_customer_resume_snaps_the_deadline_to_support_hours(
    buyer, plan, operator, hour, minute, next_day
):
    _, item = waiting(buyer, plan, operator)
    resumed = (MONDAY + timedelta(days=1)).replace(hour=hour, minute=minute)
    with time_machine.travel(resumed, tick=False):
        submit(item, buyer)
    item.refresh_from_db()
    assert item.due_at == resumed.replace(hour=10, minute=0) + timedelta(days=int(next_day))


def test_order_page_offers_an_empty_form_without_selecting_or_rendering_input(
    client, buyer, plan, operator
):
    order, item = waiting(buyer, plan, operator)
    client.force_login(buyer)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("order-detail", args=[order.order_number]))
    html = response.content.decode()
    assert input_url(order, item) in html
    assert 'name="customer_input"' in html and f'value="{event_id(item)}"' in html
    assert OLD not in html
    assert not any('"customer_input"' in query["sql"] for query in queries)
    assert (
        format_jalali(services.input_timeout_day(item.input_requested_at) - timedelta(days=1))
        in html
    )
    assert "no-store" in response["Cache-Control"] and response["Content-Encoding"] == "identity"


def test_post_returns_to_the_order_and_never_copies_input_into_messages(
    client, buyer, plan, operator, caplog
):
    order, item = waiting(buyer, plan, operator)
    client.force_login(buyer)
    response = client.post(
        input_url(order, item),
        {"customer_input": NEW, "expected_event_id": event_id(item)},
        follow=True,
    )
    item.refresh_from_db()
    assert response.redirect_chain == [(reverse("order-detail", args=[order.order_number]), 302)]
    assert item.status == "QUEUED" and item.customer_input == NEW
    assert NEW not in response.content.decode() and NEW not in caplog.text
    from apps.notifications.models import Notification

    assert all(NEW not in row.text_body + row.html_body for row in Notification.objects.all())


@pytest.mark.parametrize("payload", [{}, {"expected_event_id": "bad"}, {"expected_event_id": "-1"}])
def test_malformed_posts_render_a_safe_error(client, buyer, plan, operator, payload):
    order, item = waiting(buyer, plan, operator)
    client.force_login(buyer)
    response = client.post(input_url(order, item), {"customer_input": NEW, **payload})
    assert response.status_code == 400 and b'role="alert"' in response.content
    assert NEW not in response.content.decode() and item.events.count() == 3


def test_customer_route_checks_login_post_csrf_and_exact_pair(client, buyer, plan, operator):
    order, item = waiting(buyer, plan, operator)
    url = input_url(order, item)
    assert client.post(url).status_code == 302
    client.force_login(buyer)
    assert client.get(url).status_code == 405
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(buyer)
    assert csrf_client.post(url, {"customer_input": NEW}).status_code == 403
    assert client.post(url.replace(f"/{item.pk}/input/", "/99999999/input/")).status_code == 404
    client.force_login(User.objects.create_user("stranger@example.test"))
    assert client.post(url).status_code == 404


def test_telegram_wait_displays_last_day_and_support_without_a_customer_form(
    client, buyer, plan, operator
):
    order, item = purchase(buyer, plan, operator)
    client.force_login(buyer)
    response = client.get(reverse("order-detail", args=[order.order_number]))
    html = response.content.decode()
    last_day = services.input_timeout_day(item.input_requested_at) - timedelta(days=1)
    assert format_jalali(last_day) in html
    assert 'data-input-item="' in html and 'name="customer_input"' not in html
    assert "https://t.me/premshop_support" in html


def test_customer_input_post_is_hidden_from_html_error_reports(
    client, buyer, plan, operator, settings
):
    from django.core.signals import got_request_exception
    from django.views.debug import SafeExceptionReporterFilter

    order, item = waiting(buyer, plan, operator)
    client.force_login(buyer)
    client.raise_request_exception = False
    settings.DEBUG = False
    reported = []

    def capture(sender, request, **kwargs):
        reported.append(SafeExceptionReporterFilter().get_post_parameters(request))

    got_request_exception.connect(capture, weak=False)
    try:
        with patch(
            "apps.orders.views.submit_customer_input", side_effect=RuntimeError("input failed")
        ):
            response = client.post(
                input_url(order, item), {"customer_input": NEW, "expected_event_id": event_id(item)}
            )
    finally:
        got_request_exception.disconnect(capture)
    assert response.status_code == 500
    assert reported and reported[0]["customer_input"] != NEW
    assert NEW not in response.content.decode()


def test_waiting_item_stays_actionable_beside_another_state(client, buyer, plan, operator):
    from apps.orders.tests.test_order_detail import second_plan

    plan.input_type, plan.max_quantity = "email", 1
    plan.save()
    order = services.place_order(
        user=buyer, lines=[services.OrderLine(plan, 1, OLD), services.OrderLine(second_plan(), 1)]
    )
    from apps.orders.tests.test_input_clock import pay

    pay(order, operator)
    item = order.items.order_by("pk").first()
    act(services.request_input, item, operator)
    client.force_login(buyer)
    html = client.get(reverse("order-detail", args=[order.order_number])).content.decode()
    assert html.count('name="customer_input"') == 1 and input_url(order, item) in html


def test_last_day_is_information_and_late_input_can_still_win(client, buyer, plan, operator):
    order, item = waiting(buyer, plan, operator)
    client.force_login(buyer)
    at = MONDAY + timedelta(days=21, hours=10)
    with time_machine.travel(at, tick=False):
        client.force_login(buyer)
        response = client.post(
            input_url(order, item), {"customer_input": NEW, "expected_event_id": event_id(item)}
        )
    item.refresh_from_db()
    assert response.status_code == 302 and item.status == "QUEUED"

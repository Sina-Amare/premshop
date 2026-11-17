from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from unittest.mock import patch

import pytest
import time_machine
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.orders import services
from apps.orders.models import Delivery, OrderItem, OrderItemEvent
from apps.orders.tests.test_customer_delivery import reveal, verified
from apps.orders.tests.test_delivery import VALUE, deliver, queued
from apps.orders.tests.test_input_clock import event_id
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock
from apps.orders.tests.test_replacement import claim, reject, replace

pytestmark = pytest.mark.django_db(transaction=True)
A, B, C = "details_failed", "subscription_mismatch", "stopped_early"
LABEL_A = "اطلاعات ورود اکانت یا کد تحویل‌داده‌شده درست نیست."


def delivered(buyer, plan, operator, warranty="days_7", days=30):
    plan.product.warranty = warranty
    plan.product.save()
    order, item = queued(buyer, plan, operator)
    deliver(
        item, operator, expires_at=timezone.localdate() + timedelta(days=days) if days else None
    )
    item.refresh_from_db()
    return order, item


def submit(item, buyer, reason=A, expected=None):
    return services.open_customer_claim(
        order_id=item.order_id,
        item_id=item.pk,
        actor=buyer,
        reason=reason,
        expected_event_id=event_id(item) if expected is None else expected,
    )


def url(order, item):
    return f"/account/orders/{order.order_number}/items/{item.pk}/claim/"


@pytest.mark.parametrize("staff_buyer", [False, True])
def test_owned_filing_is_one_customer_event_even_for_staff(buyer, plan, operator, staff_buyer):
    buyer.is_staff = staff_buyer
    buyer.save()
    order, item = delivered(buyer, plan, operator)
    before = item.events.count()
    submit(item, buyer)
    item.refresh_from_db()
    event = item.events.latest("pk")
    assert item.status == "REPLACEMENT_REQUESTED" and item.events.count() == before + 1
    assert (event.actor, event.actor_user_id, event.claim_reason, event.note) == (
        "customer",
        buyer.pk,
        A,
        "",
    )
    assert Delivery.objects.filter(order_item=item).count() == 1
    assert order.user_id == buyer.pk


def test_foreign_staff_inactive_and_foreign_item_are_refused(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    other = User.objects.create_user("stranger@example.test")
    for actor in (other, operator):
        with pytest.raises(PermissionDenied):
            submit(item, actor)
    buyer.is_active = False
    buyer.save()
    with pytest.raises(PermissionDenied):
        submit(item, buyer)
    buyer.is_active = True
    buyer.save()
    with pytest.raises(PermissionDenied):
        services.open_customer_claim(
            order_id=order.pk,
            item_id=item.pk + 100000,
            actor=buyer,
            expected_event_id=event_id(item),
            reason=A,
        )
    assert item.events.count() == 3


@pytest.mark.parametrize("warranty", ["none", "days_7", "full_period"])
def test_48_hours_use_the_current_delivery_and_ignore_catalogue_edits(
    buyer, plan, operator, warranty
):
    _, item = delivered(buyer, plan, operator, warranty=warranty)
    current = item.deliveries.latest("pk").created_at
    Product.objects.filter(pk=plan.product_id).update(warranty="none")
    for reason in (A, B):
        with time_machine.travel(
            current + timedelta(hours=48) - timedelta(microseconds=1), tick=False
        ):
            submit(item, buyer, reason)
            reject(item, operator)
        with time_machine.travel(current + timedelta(hours=48), tick=False):
            with pytest.raises(ValidationError):
                submit(item, buyer, reason)
    item.refresh_from_db()
    assert item.product_snapshot["warranty"] == warranty


@pytest.mark.parametrize("reason", [B, C])
def test_no_end_date_offers_and_accepts_only_a(buyer, plan, operator, reason):
    order, item = delivered(buyer, plan, operator, days=None)
    client = Client()
    client.force_login(buyer)
    body = client.get(reverse("order-detail", args=[order.order_number])).content.decode()
    assert f'value="{A}"' in body
    assert f'value="{B}"' not in body and f'value="{C}"' not in body
    with pytest.raises(ValidationError):
        submit(item, buyer, reason)


@pytest.mark.parametrize("warranty", ["days_7", "full_period"])
def test_warranty_deadline_is_frozen_at_first_delivery_and_replacement_never_extends_it(
    buyer,
    plan,
    operator,
    warranty,
):
    order, item = delivered(buyer, plan, operator, warranty=warranty, days=10)
    first = item.delivered_at
    end = timezone.localdate(first) + timedelta(days=7 if warranty == "days_7" else 10)
    assert item.warranty_ends_on == end
    submit(item, buyer, C)
    with time_machine.travel(first + timedelta(days=2), tick=False):
        replace(item, operator, expires_at=end + timedelta(days=30))
    item.refresh_from_db()
    assert item.warranty_ends_on == end and item.delivered_at == first
    with time_machine.travel(first + timedelta(days=3), tick=False):
        submit(item, buyer, A)
        reject(item, operator)
    with time_machine.travel(first + timedelta(days=40), tick=False):
        with pytest.raises(ValidationError):
            submit(item, buyer, C)
        client = Client()
        client.force_login(buyer)
        assert (
            f'value="{C}"'
            not in client.get(reverse("order-detail", args=[order.order_number])).content.decode()
        )


def test_no_warranty_never_offers_or_accepts_c(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator, warranty="none")
    client = Client()
    client.force_login(buyer)
    assert (
        f'value="{C}"'
        not in client.get(reverse("order-detail", args=[order.order_number])).content.decode()
    )
    with pytest.raises(ValidationError):
        submit(item, buyer, C)


def test_replays_stale_pages_and_new_claims_after_resolution(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    expected = event_id(item)
    submit(item, buyer, expected=expected)
    submit(item, buyer, reason=B, expected=expected)
    assert item.events.filter(to_status="REPLACEMENT_REQUESTED").count() == 1
    assert item.events.latest("pk").claim_reason == A
    reject(item, operator)
    with pytest.raises(ValidationError):
        submit(item, buyer, expected=expected)
    submit(item, buyer)
    replace(item, operator)
    submit(item, buyer)
    assert item.events.filter(to_status="REPLACEMENT_REQUESTED").count() == 3


def test_panel_claim_by_the_same_staff_buyer_is_not_a_customer_replay(buyer, plan, operator):
    _, item = delivered(operator, plan, operator)
    expected = event_id(item)
    claim(item, operator)
    with pytest.raises(ValidationError):
        submit(item, operator, expected=expected)
    assert item.events.latest("pk").actor == "operator"


def test_audit_failure_rolls_back_customer_claim(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    with (
        patch.object(OrderItemEvent.objects, "create", side_effect=RuntimeError),
        pytest.raises(RuntimeError),
    ):
        submit(item, buyer)
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.events.count() == 3


def test_claim_post_is_owned_csrf_protected_and_validates_without_echoing_prose(
    buyer, plan, operator
):
    order, item = delivered(buyer, plan, operator)
    client = Client()
    client.force_login(operator)
    assert client.post(url(order, item)).status_code == 404
    client.force_login(buyer)
    assert client.get(url(order, item)).status_code == 405
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(buyer)
    assert strict.post(url(order, item)).status_code == 403
    for data in ({}, {"expected_event_id": event_id(item), "reason": "PRIVATE-PROSE"}):
        response = client.post(url(order, item), data)
        assert response.status_code == 400 and b"PRIVATE-PROSE" not in response.content
    response = client.post(
        url(order, item),
        {
            "expected_event_id": event_id(item),
            "reason": A,
            "note": "PRIVATE-PROSE",
            "customer_input": "PRIVATE-INPUT",
        },
    )
    assert response.status_code == 302
    event = item.events.latest("pk")
    assert event.note == "" and event.claim_reason == A


def test_customer_reveal_keeps_only_the_current_generation_through_claims(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    verified(order, buyer)
    submit(item, buyer)
    assert reveal(order, item, buyer).lines[0].value == VALUE
    reject(item, operator)
    assert reveal(order, item, buyer).lines[0].value == VALUE
    submit(item, buyer)
    replace(item, operator)
    assert reveal(order, item, buyer).lines[0].value == "replacement-secret-3J8"
    assert Delivery.objects.filter(order_item=item).count() == 2


def test_two_customer_tabs_serialize_to_one_claim(buyer, plan, operator, monkeypatch):
    _, item = delivered(buyer, plan, operator)
    expected = event_id(item)
    entered, release, pids = Event(), Event(), Queue()
    original = services._input_event

    def held(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(services, "_input_event", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, lambda: submit(item, buyer, expected=expected))
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(in_connection, pids, lambda: submit(item, buyer, B, expected))
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        first.result(timeout=15)
        second.result(timeout=15)
    assert item.events.filter(to_status="REPLACEMENT_REQUESTED").count() == 1


def test_refund_policy_ignores_new_frozen_deadline_and_ab_claims(buyer, plan, operator):
    from apps.orders.tests.test_paid_cancellation import cancel

    _, item = delivered(buyer, plan, operator, warranty="none")
    submit(item, buyer)
    assert services.PAID_CANCELLATIONS["supply_failure"] == ("QUEUED", "AWAITING_INPUT")
    for reason in ("supply_failure", "warranty_refund"):
        with pytest.raises(ValidationError):
            cancel(item, operator, reason)
    reject(item, operator)
    OrderItem.objects.filter(pk=item.pk).update(
        warranty_ends_on=timezone.localdate() - timedelta(days=2)
    )
    item.refresh_from_db()
    assert item.status == "DELIVERED"

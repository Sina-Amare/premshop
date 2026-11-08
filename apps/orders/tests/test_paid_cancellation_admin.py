from datetime import timedelta

import pytest
import time_machine
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.core.formatting import format_toman
from apps.orders.tests.test_delivery import delivery_url, queued
from apps.orders.tests.test_input_clock import event_id
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import REASON
from apps.orders.tests.test_replacement import claim, delivered
from apps.payments.models import Refund

pytestmark = pytest.mark.django_db(transaction=True)


def cancel_url(item):
    return reverse("admin:orders_order_item_action", args=[item.order_id, item.pk, "cancel"])


def signed_in(user, **options):
    client = Client(**options)
    client.force_login(user)
    return client


def post_cancel(client, item, reason="supply_failure", **changes):
    data = {"expected_event_id": event_id(item), "reason": reason, "note": REASON}
    return client.post(cancel_url(item), {**data, **changes})


def offered(body):
    return [
        r for r in ("supply_failure", "input_timeout", "warranty_refund") if f'value="{r}"' in body
    ]


def clerk(*codenames):
    user = User.objects.create_user("clerk@example.test", is_staff=True)
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


def test_a_queued_items_page_offers_a_supply_failure_refund_of_its_paid_amount(
    buyer, plan, operator
):
    _, item = queued(buyer, plan, operator)
    body = signed_in(operator).get(delivery_url(item)).content.decode()
    assert cancel_url(item) in body and offered(body) == ["supply_failure"]
    assert f"مبلغ بازپرداخت: {format_toman(200000)}" in body


def test_a_waiting_item_offers_missing_details_only_from_day_21(buyer, plan, operator):
    _, item = queued(buyer, plan, operator, telegram=True)
    page = signed_in(operator).get(delivery_url(item)).content.decode()
    assert offered(page) == ["supply_failure"]
    with time_machine.travel(item.created_at + timedelta(days=22)):
        page = signed_in(operator).get(delivery_url(item)).content.decode()
    assert offered(page) == ["supply_failure", "input_timeout"]


def test_a_claim_offers_its_unused_day_refund_inside_the_warranty_only(buyer, plan, operator):
    Product.objects.filter(pk=plan.product_id).update(warranty="none")
    _, item = delivered(buyer, plan, operator)
    claim(item, operator)
    body = signed_in(operator).get(delivery_url(item)).content.decode()
    assert cancel_url(item) not in body and offered(body) == []
    assert "خارج از مدت گارانتی" in body


def test_a_delivered_item_without_a_claim_has_no_cancel_form(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    body = signed_in(operator).get(delivery_url(item)).content.decode()
    assert cancel_url(item) not in body


def test_cancelling_lists_the_owed_refund_on_the_order_page(buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    client = signed_in(operator)
    response = post_cancel(client, item)
    detail = reverse("admin:orders_order_change", args=[order.pk])
    assert response.status_code == 302 and response["Location"] == detail
    body = client.get(detail).content.decode()
    assert f"{format_toman(200000)} · در انتظار واریز" in body
    assert Refund.objects.get().order_item_id == item.pk


def test_a_refused_cancellation_explains_itself_and_writes_nothing(buyer, plan, operator):
    _, item = queued(buyer, plan, operator, telegram=True)
    response = post_cancel(signed_in(operator), item, "input_timeout")
    assert response.status_code == 400 and "۲۱ روز" in response.content.decode()
    item.refresh_from_db()
    assert item.status == "AWAITING_INPUT" and not Refund.objects.exists()


def test_staff_who_may_not_record_refunds_see_no_form_and_cannot_post(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    client = signed_in(clerk("view_order", "change_order", "change_orderitem"))
    assert cancel_url(item) not in client.get(delivery_url(item)).content.decode()
    assert post_cancel(client, item).status_code == 403
    assert not Refund.objects.exists()


def test_a_cancellation_without_its_csrf_token_is_refused(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    response = post_cancel(signed_in(operator, enforce_csrf_checks=True), item)
    assert response.status_code == 403 and not Refund.objects.exists()


def test_the_order_page_links_a_waiting_item_to_its_cancel_form(buyer, plan, operator):
    order, item = queued(buyer, plan, operator, telegram=True)
    body = signed_in(operator).get(reverse("admin:orders_order_change", args=[order.pk]))
    assert f'href="{delivery_url(item)}"' in body.content.decode()

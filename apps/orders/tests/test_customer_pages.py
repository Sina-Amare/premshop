from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.core.formatting import format_toman, to_persian_digits
from apps.orders import services
from apps.orders.models import OrderItem, OrderItemEvent
from apps.orders.services import OrderLine

pytestmark = pytest.mark.django_db


def purchase(buyer, plan, quantity=1):
    return services.place_order(
        user=buyer, lines=[OrderLine(plan, quantity)], activation_consent=True
    )


def privacy_headers(response):
    assert "no-store" in response["Cache-Control"]
    assert "noindex" in response["X-Robots-Tag"]
    assert response["Referrer-Policy"] == "no-referrer"


def test_order_list_and_account_current_summary_are_owner_only(client, buyer, plan):
    order = purchase(buyer, plan, 2)
    other = User.objects.create_user("other@example.test")
    hidden = purchase(other, plan)
    client.force_login(buyer)
    response = client.get(reverse("order-list"))
    body = response.content.decode()
    assert response.status_code == 200
    assert to_persian_digits(order.order_number) in body
    assert to_persian_digits(hidden.order_number) not in body
    assert "سفارش‌ها" in body and "منتظر پرداخت" in body
    assert reverse("order-detail", args=[order.order_number]) in body
    account = client.get(reverse("account"))
    assert reverse("order-detail", args=[order.order_number]) in account.content.decode()
    assert reverse("order-detail", args=[hidden.order_number]) not in account.content.decode()


def test_detail_groups_frozen_category_facts_and_represents_order_total_once(client, buyer, plan):
    order = purchase(buyer, plan, 2)
    frozen_name, frozen_category, frozen_title = (
        plan.product.name,
        plan.product.category.name,
        plan.title,
    )
    Product.objects.filter(pk=plan.product_id).update(name="نام جدید", status=Product.Status.DRAFT)
    type(plan.product.category).objects.filter(pk=plan.product.category_id).update(
        name="دسته‌ی جدید"
    )
    Plan.objects.filter(pk=plan.pk).update(title="پلن جدید", sale_price=1)
    client.force_login(buyer)
    response = client.get(reverse("order-detail", args=[order.order_number]))
    body = response.content.decode()
    assert all(value in body for value in (frozen_name, frozen_category, frozen_title, "۷ روز"))
    assert "نام جدید" not in body and "پلن جدید" not in body
    assert body.count("data-order-total") == 1
    assert "تعداد: ۲" in body
    assert "قیمت هر عدد" in body and format_toman(200000, with_unit=False) in body
    assert "هنوز پرداخت نشده" in body


@pytest.mark.parametrize("endpoint", ["order-detail", "order-invoice", "order-cancel"])
def test_another_customer_and_staff_receive_no_order_details(client, buyer, plan, endpoint):
    order = purchase(buyer, plan)
    stranger = User.objects.create_user("staff@example.test", is_staff=True)
    client.force_login(stranger)
    url = reverse(endpoint, args=[order.order_number])
    response = (
        client.post(url, {"confirm": "yes"}) if endpoint == "order-cancel" else client.get(url)
    )
    assert response.status_code == 404
    assert to_persian_digits(order.order_number) not in response.content.decode()
    assert order.tracking_token not in response.content.decode()
    assert order.items.get().status == OrderItem.Status.PENDING_PAYMENT


def test_cancellation_requires_post_confirmation_csrf_and_releases_whole_order_once(
    client, buyer, plan
):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    order = purchase(buyer, plan, 2)
    url = reverse("order-cancel", args=[order.order_number])
    client.force_login(buyer)
    assert client.get(url).status_code == 405
    assert client.post(url).status_code == 400
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(buyer)
    assert csrf.post(url, {"confirm": "yes"}).status_code == 403
    assert client.post(url, {"confirm": "yes"}).status_code == 302
    assert client.post(url, {"confirm": "yes"}).status_code == 302
    assert set(order.items.values_list("status", flat=True)) == {OrderItem.Status.CANCELLED}
    plan.refresh_from_db()
    assert plan.stock_quantity == 3
    assert OrderItemEvent.objects.filter(order_item__order=order).count() == 4


def test_terminal_order_cannot_be_cancelled_and_overdue_get_never_mutates(client, buyer, plan):
    order = purchase(buyer, plan)
    type(order).objects.filter(pk=order.pk).update(
        unpaid_expires_at=timezone.now() - timedelta(seconds=1)
    )
    client.force_login(buyer)
    response = client.get(reverse("order-detail", args=[order.order_number]))
    assert "مهلت پرداخت تمام شده" in response.content.decode()
    assert order.items.get().status == OrderItem.Status.PENDING_PAYMENT
    order.items.update(status=OrderItem.Status.QUEUED)
    assert (
        client.post(
            reverse("order-cancel", args=[order.order_number]), {"confirm": "yes"}
        ).status_code
        == 400
    )
    assert order.items.get().status == OrderItem.Status.QUEUED


def test_invoice_is_standalone_owner_only_and_has_no_private_values(client, buyer, plan):
    order = purchase(buyer, plan, 2)
    order.items.update(customer_input="DO-NOT-RENDER-INPUT")
    OrderItemEvent.objects.create(
        order_item=order.items.first(),
        from_status=None,
        to_status=OrderItem.Status.PENDING_PAYMENT,
        actor=OrderItemEvent.Actor.OPERATOR,
        note="DO-NOT-RENDER-NOTE",
    )
    client.force_login(buyer)
    response = client.get(reverse("order-invoice", args=[order.order_number]))
    body = response.content.decode()
    assert response.status_code == 200
    privacy_headers(response)
    assert "فاکتور سفارش" in body and "این فاکتور رسید پرداخت نیست." in body
    assert "دانلود تصویر فاکتور" in body and "منتظر پرداخت" in body
    assert to_persian_digits(order.order_number) in body
    assert "تعداد: ۲" in body
    for forbidden in (
        buyer.email,
        order.tracking_token,
        "DO-NOT-RENDER-INPUT",
        "DO-NOT-RENDER-NOTE",
        "@premshop_support",
        "100000",
    ):
        assert forbidden not in body
    assert set(response.context["invoice_data"]) == {
        "number",
        "date",
        "status",
        "lines",
        "subtotal",
        "discount",
        "total",
        "notice",
    }
    services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    assert (
        "لغو شده"
        in client.get(reverse("order-invoice", args=[order.order_number])).content.decode()
    )


def test_tracking_is_an_allowlist_of_frozen_names_statuses_and_times(client, buyer, plan):
    order = purchase(buyer, plan)
    order.items.update(customer_input="PRIVATE-INPUT")
    OrderItemEvent.objects.create(
        order_item=order.items.first(),
        from_status=None,
        to_status=OrderItem.Status.PENDING_PAYMENT,
        actor=OrderItemEvent.Actor.OPERATOR,
        note="PRIVATE-NOTE",
    )
    response = client.get(reverse("order-tracking", args=[order.tracking_token]))
    body = response.content.decode()
    assert response.status_code == 200
    privacy_headers(response)
    assert plan.product.name in body and "منتظر پرداخت" in body
    for forbidden in (
        buyer.email,
        order.tracking_token,
        plan.title,
        "PRIVATE-INPUT",
        "PRIVATE-NOTE",
        str(order.order_number),
        to_persian_digits(order.order_number),
        "200000",
        format_toman(200000),
        "100000",
        "فاکتور",
        "@premshop_support",
    ):
        assert forbidden not in body
    assert set(response.context["tracking"][0]) == {"name", "status", "created_at", "timeline"}
    assert set(response.context["tracking"][0]["timeline"][0]) == {"status", "at"}


def test_invalid_tracking_is_uniform_and_rate_limits_use_no_raw_tokens(client, buyer, plan):
    order = purchase(buyer, plan)
    first = client.get("/track/not-a-token/")
    second = client.get("/track/" + "x" * 150 + "/")
    assert first.status_code == second.status_code == 404
    assert first.content == second.content
    privacy_headers(first)
    url = reverse("order-tracking", args=[order.tracking_token])
    cache.clear()
    for _ in range(30):
        assert client.get(url).status_code == 200
    limited = client.get(url)
    assert limited.status_code == 429
    privacy_headers(limited)
    assert order.tracking_token not in repr(cache._cache)


def test_tracking_cache_and_unexpected_errors_keep_private_headers_and_body(
    client, buyer, plan, settings
):
    order = purchase(buyer, plan)
    url = reverse("order-tracking", args=[order.tracking_token])
    with patch("apps.core.ratelimit.cache.add", side_effect=RedisConnectionError(url)):
        response = client.get(url)
    assert response.status_code == 503
    privacy_headers(response)
    assert order.tracking_token not in response.content.decode()
    assert "@premshop_support" not in response.content.decode()
    client.raise_request_exception = False
    settings.DEBUG = False
    with patch("apps.orders.views.order_for_tracking", side_effect=RuntimeError(url)):
        response = client.get(url)
    assert response.status_code == 500
    privacy_headers(response)
    assert order.tracking_token not in response.content.decode()
    assert "@premshop_support" not in response.content.decode()


def test_empty_order_list_is_useful_without_javascript(client, buyer):
    client.force_login(buyer)
    response = client.get(reverse("order-list"))
    assert "هنوز سفارشی ثبت نکرده‌اید." in response.content.decode()
    assert reverse("home") in response.content.decode()


def test_customer_projections_do_not_read_encrypted_inputs(client, buyer, plan):
    order = purchase(buyer, plan)
    client.force_login(buyer)
    with patch(
        "apps.core.fields.EncryptedTextField.from_db_value",
        side_effect=AssertionError("Private field read"),
    ):
        for name, args in [
            ("order-list", []),
            ("order-detail", [order.order_number]),
            ("order-invoice", [order.order_number]),
            ("order-tracking", [order.tracking_token]),
        ]:
            assert client.get(reverse(name, args=args)).status_code == 200


def test_tracking_method_errors_keep_http_and_privacy_headers(client, buyer, plan):
    order = purchase(buyer, plan)
    response = client.put(reverse("order-tracking", args=[order.tracking_token]))
    assert response.status_code == 405
    assert response["Allow"] == "GET"
    privacy_headers(response)
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"


def test_token_budget_applies_across_networks(client, buyer, plan):
    order = purchase(buyer, plan)
    url = reverse("order-tracking", args=[order.tracking_token])
    for network in ("192.0.2.1", "192.0.2.2"):
        for _ in range(30):
            assert client.get(url, REMOTE_ADDR=network).status_code == 200
    response = client.get(url, REMOTE_ADDR="192.0.2.3")
    assert response.status_code == 429 and response["Retry-After"] == "60"


def test_order_history_is_paginated_without_splitting_one_order(client, buyer, plan):
    for _ in range(22):
        order = purchase(buyer, plan)
        services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    client.force_login(buyer)
    first = client.get(reverse("order-list"))
    last = client.get(reverse("order-list"), {"page": "2"})
    assert len(first.context["summaries"]) == 20
    assert len(last.context["summaries"]) == 2
    assert first.content.decode().count("data-order-total") == 20
    assert last.content.decode().count("data-order-total") == 2


def test_legacy_category_fallback_and_mixed_terminal_status_use_only_frozen_facts():
    from apps.orders.selectors import grouped_items, status_label

    item = OrderItem(
        product_snapshot={"name": "محصول قبلی", "plan_title": "پلن قبلی"},
        price_snapshot=Decimal(200000),
        status=OrderItem.Status.DELIVERED,
    )
    cancelled = OrderItem(
        product_snapshot=item.product_snapshot,
        price_snapshot=item.price_snapshot,
        status=OrderItem.Status.CANCELLED,
    )
    groups = grouped_items([item, cancelled])
    assert groups[0]["name"] == "محصولات سفارش"
    assert len(groups[0]["lines"]) == 2
    assert status_label([item.status, cancelled.status]) == "پایان‌یافته"
    cancelled.status = OrderItem.Status.PENDING_PAYMENT
    assert status_label([item.status, cancelled.status]) == "منتظر پرداخت"


def test_public_tracking_does_not_distinguish_one_from_repeated_identical_units(
    client, buyer, plan
):
    import time_machine

    other = User.objects.create_user("one-unit@example.test")
    with time_machine.travel("2026-10-29 08:00:00", tick=False):
        one = purchase(other, plan)
        many = purchase(buyer, plan, 2)
    first = client.get(reverse("order-tracking", args=[one.tracking_token]))
    second = client.get(reverse("order-tracking", args=[many.tracking_token]))
    assert len(first.context["tracking"]) == len(second.context["tracking"]) == 1
    assert first.content == second.content


def test_invoice_identifies_each_line_in_a_mixed_terminal_order(client, buyer, plan):
    order = purchase(buyer, plan, 2)
    first, second = order.items.order_by("pk")
    OrderItem.objects.filter(pk=first.pk).update(status=OrderItem.Status.DELIVERED)
    OrderItem.objects.filter(pk=second.pk).update(
        status=OrderItem.Status.CANCELLED,
        cancel_reason=OrderItem.CancelReason.CUSTOMER_BEFORE_PAYMENT,
    )
    client.force_login(buyer)
    response = client.get(reverse("order-invoice", args=[order.order_number]))
    lines = response.context["invoice_data"]["lines"]
    assert {line["status"] for line in lines} == {"تحویل داده شده", "لغو شده"}
    body = response.content.decode()
    assert "وضعیت: تحویل داده شده" in body and "وضعیت: لغو شده" in body


def test_https_redirects_also_protect_private_tracking_headers(client, settings):
    settings.SECURE_SSL_REDIRECT = True
    response = client.get("/track/redirect-token/")
    assert response.status_code == 301
    privacy_headers(response)

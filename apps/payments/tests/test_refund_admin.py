from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override
from django.views.debug import ExceptionReporter

from apps.accounts.models import User
from apps.core.formatting import format_jalali
from apps.orders.models import Order, OrderItemEvent
from apps.orders.services import OrderLine
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import cancel, paid_order
from apps.payments.models import Payment, Refund
from apps.payments.tests.test_refund_execution import execute
from apps.payments.tests.test_refund_execution import owed as owed

pytestmark = pytest.mark.django_db


def url(order):
    return reverse("admin:orders_order_execute_refunds", args=[order.pk])


def report_url():
    return reverse("admin:orders_order_reconciliation")


def post_data(refunds):
    now = timezone.localtime()
    return {
        "refund_ids": [row.pk for row in refunds],
        "amount": str(sum(row.amount for row in refunds)),
        "destination_kind": "card",
        "destination_last4": "6037",
        "bank_ref": "bank-private-735192",
        "bank_date": format_jalali(now, with_month_name=False),
        "bank_time": now.strftime("%H:%M:%S"),
    }


def test_the_form_requires_csrf_and_get_does_not_execute(owed, operator):
    order, refunds = owed
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    before = OrderItemEvent.objects.count()
    response = client.get(url(order))
    assert response.status_code == 200 and "no-store" in response["Cache-Control"]
    assert Refund.objects.filter(status="owed").count() == 3
    assert OrderItemEvent.objects.count() == before
    data = post_data(refunds[:2])
    assert client.post(url(order), data).status_code == 403
    data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    assert client.post(url(order), data).status_code == 302
    assert Refund.objects.filter(status="executed").count() == 2
    assert order.items.filter(status="REFUNDED").count() == 2
    assert (
        client.put(url(order), HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code
        == 405
    )


def test_mismatch_and_repeated_posts_keep_values_and_do_not_execute_other_refunds(
    client, owed, operator
):
    order, refunds = owed
    client.force_login(operator)
    data = post_data(refunds[:1])
    bad = client.post(url(order), {**data, "amount": "199999"})
    assert bad.status_code == 400 and bad.context["form"].errors
    assert bad.context["form"].data["bank_ref"] == data["bank_ref"]
    assert not Refund.objects.filter(status="executed").exists()
    assert client.post(url(order), data).status_code == 302
    response = client.post(url(order), post_data(refunds[:2]))
    assert response.status_code == 400 and "قبلاً" in response.content.decode()
    assert Refund.objects.filter(status="executed").count() == 1


def test_foreign_refund_ids_and_object_permission_are_checked(
    client, owed, buyer, plan, operator, monkeypatch
):
    order, refunds = owed
    other, items = paid_order(buyer, operator, [OrderLine(plan, 1)])
    cancel(items[0], operator)
    foreign = Refund.objects.get(order_item=items[0])
    client.force_login(operator)
    assert client.post(url(order), post_data([foreign])).status_code == 400
    model_admin = admin.site._registry[Order]
    original = model_admin.has_view_permission
    monkeypatch.setattr(
        model_admin,
        "has_view_permission",
        lambda request, obj=None: obj is None and original(request),
    )
    assert client.get(url(order)).status_code == 403
    assert client.post(url(order), post_data(refunds)).status_code == 403
    assert not Refund.objects.filter(status="executed").exists()
    assert other.pk != order.pk


@pytest.mark.parametrize("perm", [None, "view_order", "view_payment", "view_refund"])
def test_financial_views_refuse_staff_without_the_required_permissions(client, owed, perm):
    order, refunds = owed
    staff = User.objects.create_user("viewer@example.test", is_staff=True)
    if perm:
        staff.user_permissions.add(Permission.objects.get(codename=perm))
    client.force_login(staff)
    assert client.get(url(order)).status_code == 403
    assert client.post(url(order), post_data(refunds)).status_code == 403
    assert client.get(report_url()).status_code == 403


def test_daily_page_is_read_only_validates_jalali_dates_and_is_linked_from_navigation(
    client, owed, operator
):
    order, _ = owed
    client.force_login(operator)
    before = OrderItemEvent.objects.count()
    response = client.get(report_url())
    assert response.status_code == 200 and "no-store" in response["Cache-Control"]
    assert response.context["payment_total"] == response.context["owed_total"] == order.total_amount
    assert response.context["sent_total"] == 0
    assert report_url() in client.get(reverse("admin:index")).content.decode()
    assert client.post(report_url()).status_code == 405
    for day in ["not-a-date", "1405/13/32", "9999/12/30"]:
        assert client.get(report_url(), {"day": day}).status_code == 400
    assert OrderItemEvent.objects.count() == before


@pytest.mark.parametrize("kind", ["payment", "refund"])
def test_duplicates_warn_after_a_valid_save_without_refusing_it(
    client, owed, buyer, plan, operator, kind
):
    order, refunds = owed
    first, _ = paid_order(buyer, operator, [OrderLine(plan, 1)])
    Payment.objects.filter(order=first).update(bank_ref="bank-private-۷۳۵۱۹۲")
    client.force_login(operator)
    if kind == "refund":
        response = client.post(url(order), post_data(refunds), follow=True)
        assert Refund.objects.filter(status="executed").count() == 3
    else:
        from apps.orders.services import place_order

        unpaid = place_order(user=buyer, lines=[OrderLine(plan, 1)])
        response = client.post(
            reverse("admin:orders_order_record_manual_payment", args=[unpaid.pk]),
            {"amount": unpaid.total_amount, "bank_ref": "bank-private-735192"},
            follow=True,
        )
        assert Payment.objects.get(order=unpaid).status == "verified"
    assert response.status_code == 200
    assert any(message.level_tag == "warning" for message in response.context["messages"])


def test_private_transfer_facts_never_appear_in_customer_projections(client, owed, operator, buyer):
    order, refunds = owed
    execute(order, refunds, operator, destination_last4="9813", bank_ref="private-ref-never-public")
    client.force_login(buyer)
    for name, args in [
        ("order-detail", [order.order_number]),
        ("order-list", []),
        ("order-tracking", [order.tracking_token]),
        ("order-invoice", [order.order_number]),
    ]:
        response = client.get(reverse(name, args=args))
        assert response.status_code == 200
        assert "private-ref-never-public" not in response.content.decode()
    assert client.get(url(order)).status_code == client.get(report_url()).status_code == 302
    assert not OrderItemEvent.objects.filter(note__contains="private-ref-never-public").exists()


@pytest.mark.parametrize("kind", ["payment", "refund"])
def test_a_forced_exception_hides_post_and_traceback_values_from_django_reports(
    client, owed, buyer, plan, operator, monkeypatch, settings, kind
):
    from apps.payments import services

    settings.DEBUG = False
    reference = "private-traceback-" + uuid4().hex
    order, refunds = owed
    if kind == "payment":
        from apps.orders.services import place_order

        order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
        target = reverse("admin:orders_order_record_manual_payment", args=[order.pk])
        data = {"amount": order.total_amount, "bank_ref": reference}
    else:
        target = url(order)
        data = {**post_data(refunds), "bank_ref": reference}
    monkeypatch.setattr(services, "_bank_reference", lambda bank_ref: 1 / 0)
    client.force_login(operator)
    client.raise_request_exception = False
    response = client.post(target, data)
    assert response.status_code == 500
    reporter = ExceptionReporter(response.wsgi_request, *response.exc_info)
    assert reference not in reporter.get_traceback_text()
    assert reference not in reporter.get_traceback_html()


@override("fa")
def test_new_forms_follow_the_panel_language(client, owed, operator):
    order, _ = owed
    client.force_login(operator)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    assert "Record refund transfer" in client.get(url(order)).content.decode()
    assert "Daily reconciliation" in client.get(report_url()).content.decode()


def test_the_report_and_reference_links_respect_the_admin_queryset(
    client, owed, buyer, plan, operator, monkeypatch
):
    order, refunds = owed
    foreign, _ = paid_order(buyer, operator, [OrderLine(plan, 1)])
    Payment.objects.filter(order=foreign).update(bank_ref="hidden-other-order-reference")
    execute(order, refunds, operator, bank_ref="hidden-other-order-reference")
    model_admin = admin.site._registry[Order]
    original = model_admin.get_queryset
    monkeypatch.setattr(
        model_admin, "get_queryset", lambda request: original(request).filter(pk=order.pk)
    )
    client.force_login(operator)
    response = client.get(report_url())
    assert response.status_code == 200
    assert len(response.context["payments"]) == 1
    assert all(not row["duplicates"] for row in response.context["refunds"])
    assert reverse("admin:orders_order_change", args=[foreign.pk]) not in response.content.decode()

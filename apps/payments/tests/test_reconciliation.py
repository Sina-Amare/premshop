from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import time_machine

from apps.orders.clock import TEHRAN
from apps.orders.services import OrderLine
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import cancel, paid_order
from apps.payments.models import Payment, Refund
from apps.payments.tests.test_refund_execution import execute

pytestmark = pytest.mark.django_db


def test_the_report_uses_tehran_boundaries_and_sums_each_grouped_refund_once(buyer, plan, operator):
    from apps.payments.selectors import reconciliation

    midnight = datetime(2026, 11, 9, tzinfo=TEHRAN)
    with time_machine.travel(midnight - timedelta(minutes=1), tick=False):
        order, items = paid_order(buyer, operator, [OrderLine(plan, 2)])
        for item in items:
            cancel(item, operator)
    refunds = list(Refund.objects.order_by("pk"))
    # Enter tomorrow, with the bank's time of exactly midnight today.
    with time_machine.travel(midnight + timedelta(days=1, hours=1), tick=False):
        execute(order, refunds, operator, executed_at=midnight)
    yesterday = reconciliation(date(2026, 11, 8))
    today = reconciliation(date(2026, 11, 9))
    tomorrow = reconciliation(date(2026, 11, 10))
    assert (yesterday["payment_total"], yesterday["owed_total"], yesterday["sent_total"]) == (
        400000,
        400000,
        0,
    )
    assert (today["payment_total"], today["owed_total"], today["sent_total"]) == (0, 0, 400000)
    assert len(today["refunds"]) == 2
    assert all(not row["owed_today"] and row["sent_today"] for row in today["refunds"])
    assert not tomorrow["payments"] and not tomorrow["refunds"]
    assert sum(row["refund"].amount for row in today["refunds"]) == today["sent_total"]


def test_a_refund_owed_and_sent_on_the_day_appears_once_with_two_flags(buyer, plan, operator):
    from apps.payments.selectors import reconciliation

    with time_machine.travel(datetime(2026, 11, 9, 0, 1, tzinfo=TEHRAN), tick=False):
        order, items = paid_order(buyer, operator, [OrderLine(plan, 1)])
        cancel(items[0], operator)
        execute(order, [Refund.objects.get()], operator)
    report = reconciliation(date(2026, 11, 9))
    assert len(report["payments"]) == len(report["refunds"]) == 1
    assert report["refunds"][0]["owed_today"] and report["refunds"][0]["sent_today"]
    assert (report["payment_total"], report["owed_total"], report["sent_total"]) == (
        200000,
        200000,
        200000,
    )


def test_reference_matches_cross_record_types_and_days_but_exclude_the_same_order(
    buyer, plan, operator
):
    from apps.payments.selectors import reconciliation, reference_matches

    earlier = datetime(2026, 11, 7, 12, tzinfo=TEHRAN)
    with time_machine.travel(earlier, tick=False):
        first, _ = paid_order(buyer, operator, [OrderLine(plan, 1)])
        Payment.objects.filter(order=first).update(bank_ref="TR- ۱۲\t٣")
    with time_machine.travel(earlier + timedelta(days=1), tick=False):
        second, items = paid_order(buyer, operator, [OrderLine(plan, 2)])
        for item in items:
            cancel(item, operator)
        refunds = list(Refund.objects.order_by("pk"))
        execute(second, refunds, operator, bank_ref="TR-123")
    assert reference_matches("TR-۱٢۳", second.pk) == {first.pk: first.order_number}
    report = reconciliation(date(2026, 11, 8))
    assert all(row["duplicates"] == {first.pk: first.order_number} for row in report["refunds"])
    assert reference_matches("not-there", second.pk) == {}
    assert reference_matches("", second.pk) == {}


def test_reference_matching_does_not_remove_punctuation_or_change_case(buyer, plan, operator):
    from apps.payments.selectors import reference_matches

    order, _ = paid_order(buyer, operator, [OrderLine(plan, 1)])
    Payment.objects.filter(order=order).update(bank_ref="Ref-0012")
    assert reference_matches("Ref-۰۰۱۲", -1) == {order.pk: order.order_number}
    assert reference_matches("ref-0012", -1) == {}
    assert reference_matches("Ref0012", -1) == {}


def test_only_verified_payments_and_executed_refunds_have_bank_activity(buyer, plan, operator):
    from apps.payments.selectors import reconciliation, reference_matches

    order, items = paid_order(buyer, operator, [OrderLine(plan, 1)])
    cancel(items[0], operator)
    Payment.objects.create(order=order, method="gateway", amount=Decimal(1), bank_ref="pending-ref")
    report = reconciliation(order.created_at.astimezone(TEHRAN).date())
    assert len(report["payments"]) == 1
    assert report["owed_total"] == 200000 and report["sent_total"] == 0
    assert reference_matches("pending-ref", -1) == {}

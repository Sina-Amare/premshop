from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Q, QuerySet

from apps.orders.models import Order
from apps.orders.selectors import item_numbers
from apps.payments.models import Payment, Refund

TEHRAN = ZoneInfo("Asia/Tehran")


def reference_key(reference: str) -> str:
    digits = reference.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    return "".join(digits.split())


def reference_index(orders: QuerySet[Order] | None = None) -> dict[str, dict[int, int]]:
    scope = Order.objects.all() if orders is None else orders
    index: dict[str, dict[int, int]] = defaultdict(dict)
    payments = Payment.objects.filter(status=Payment.Status.VERIFIED, order__in=scope).values_list(
        "bank_ref", "order_id", "order__order_number"
    )
    refunds = Refund.objects.filter(
        status=Refund.Status.EXECUTED, order_item__order__in=scope
    ).values_list("bank_ref", "order_item__order_id", "order_item__order__order_number")
    # shortcut: scan the small ledger; use an indexed comparison key when its size warrants it.
    for rows in (payments, refunds):
        for reference, order_id, number in rows:
            if key := reference_key(reference):
                index[key][order_id] = number
    return dict(index)


def reference_matches(
    reference: str, order_id: int, *, orders: QuerySet[Order] | None = None
) -> dict[int, int]:
    return {
        pk: number
        for pk, number in reference_index(orders).get(reference_key(reference), {}).items()
        if pk != order_id
    }


def reconciliation(day: date, *, orders: QuerySet[Order] | None = None) -> dict[str, Any]:
    scope = Order.objects.all() if orders is None else orders
    start = datetime.combine(day, time.min, TEHRAN)
    end = datetime.combine(day + timedelta(days=1), time.min, TEHRAN)
    index = reference_index(scope)

    def duplicates(reference: str, order_id: int) -> dict[int, int]:
        return {
            pk: number
            for pk, number in index.get(reference_key(reference), {}).items()
            if pk != order_id
        }

    payments: list[dict[str, Any]] = [
        {"payment": row, "duplicates": duplicates(row.bank_ref, row.order_id)}
        for row in Payment.objects.filter(
            status=Payment.Status.VERIFIED,
            verified_at__gte=start,
            verified_at__lt=end,
            order__in=scope,
        ).select_related("order", "matched_by")
    ]
    refunds: list[dict[str, Any]] = [
        {
            "refund": row,
            "owed_today": start <= row.created_at < end,
            "sent_today": row.executed_at is not None and start <= row.executed_at < end,
            "duplicates": duplicates(row.bank_ref, row.order_item.order_id),
        }
        for row in Refund.objects.filter(
            Q(created_at__gte=start, created_at__lt=end)
            | Q(executed_at__gte=start, executed_at__lt=end)
        )
        .filter(order_item__order__in=scope)
        .select_related("order_item__order", "created_by", "executed_by")
        .defer("order_item__customer_input")
    ]
    return {
        "payments": payments,
        "refunds": refunds,
        "item_numbers": item_numbers({row["refund"].order_item.order_id for row in refunds}),
        "payment_total": sum((row["payment"].amount for row in payments), Decimal(0)),
        "owed_total": sum(
            (row["refund"].amount for row in refunds if row["owed_today"]), Decimal(0)
        ),
        "sent_total": sum(
            (row["refund"].amount for row in refunds if row["sent_today"]), Decimal(0)
        ),
    }

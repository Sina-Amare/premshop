import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from queue import Queue
from threading import Event
from unittest.mock import patch

import pytest
import time_machine
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.orders import services
from apps.orders.clock import TEHRAN
from apps.orders.models import DiscountCode, DiscountRedemption
from apps.orders.services import OrderLine
from apps.orders.tests.test_delivery import BANK_REF, deliver, queued
from apps.orders.tests.test_input_clock import act, event_id
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import (
    in_connection,
    separate_plan,
    wait_for_lock,
)
from apps.orders.tests.test_replacement import claim, delivered
from apps.payments.models import Refund
from apps.payments.services import record_manual_payment

pytestmark = pytest.mark.django_db(transaction=True)

REASON = "تأمین‌کننده این پلن را دیگر نمی‌فروشد"


def cancel(item, actor, reason="supply_failure", **changes):
    return act(
        services.cancel_paid_item,
        item,
        actor,
        reason=reason,
        note=changes.pop("note", REASON),
        **changes,
    )


def paid_order(buyer, operator, lines, code=""):
    order = services.place_order(user=buyer, lines=lines, discount_code=code)
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref=BANK_REF
    )
    return order, list(order.items.order_by("pk"))


def tehran(year, month, day):
    # 01:00 in Tehran is still the previous day in UTC, so a UTC date reads a day early.
    return time_machine.travel(datetime(year, month, day, 1, tzinfo=TEHRAN), tick=False)


# Each item's share of the discount


def test_a_discount_is_shared_by_price_with_leftover_tomans_to_the_lowest_item_ids(buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(sale_price=100000)
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=10000)
    order = services.place_order(user=buyer, lines=[OrderLine(plan, 3)], discount_code="SAVE")
    items = list(order.items.order_by("pk"))
    assert [item.discount_share for item in items] == [3334, 3333, 3333]
    assert [item.paid_amount for item in items] == [96666, 96667, 96667]
    assert sum(item.paid_amount for item in items) == order.total_amount == 290000


def test_items_a_code_did_not_cover_carry_no_share(buyer, plan):
    other = separate_plan(plan)
    Plan.objects.filter(pk=plan.pk).update(sale_price=300000)
    Plan.objects.filter(pk=other.pk).update(sale_price=100000)
    code = DiscountCode.objects.create(code="SAVE", kind="percent", value=10, scope="selected")
    code.products.add(plan.product)
    order = services.place_order(
        user=buyer, lines=[OrderLine(plan, 2), OrderLine(other, 1)], discount_code="SAVE"
    )
    shares = sorted(order.items.values_list("plan_id", "discount_share"))
    assert shares == sorted([(plan.pk, 30000), (plan.pk, 30000), (other.pk, 0)])


def test_a_free_item_takes_no_leftover_toman_at_checkout(buyer, plan):
    other = separate_plan(plan)
    Plan.objects.filter(pk=plan.pk).update(sale_price=0)
    Plan.objects.filter(pk=other.pk).update(sale_price=30000, stock_quantity=None, max_quantity=3)
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=10001)
    order = services.place_order(
        user=buyer, lines=[OrderLine(plan, 1), OrderLine(other, 2)], discount_code="SAVE"
    )
    items = list(order.items.order_by("pk"))
    assert items[0].plan_id == plan.pk  # the free item has the lowest ID
    assert [item.discount_share for item in items] == [0, 5001, 5000]


def test_shares_always_add_up_to_the_discount_and_never_exceed_a_price():
    rng = random.Random(7)  # noqa: S311 - repeatable test data, not a secret
    for _ in range(500):
        ids = rng.sample(range(1, 10000), rng.randint(1, 8))
        # A free plan is allowed, so one price in three is zero.
        prices = {pk: Decimal(rng.choice([0, rng.randint(1, 900000)])) for pk in ids}
        discount = Decimal(rng.randint(0, int(sum(prices.values()))))
        shares = services.discount_shares(prices, discount)
        assert set(shares) == set(prices)
        assert sum(shares.values()) == discount
        assert all(0 <= shares[pk] <= prices[pk] for pk in prices)


# Which reason cancels from which status

STATES = ["pending", "queued", "waiting", "delivered", "claimed", "cancelled"]
ALLOWED = {
    ("queued", "supply_failure"),
    ("waiting", "supply_failure"),
    ("waiting", "input_timeout"),
    ("claimed", "warranty_refund"),
}


def item_in(state, buyer, plan, operator):
    if state == "pending":
        return services.place_order(user=buyer, lines=[OrderLine(plan, 1)]).items.get()
    if state == "waiting":
        _, item = queued(buyer, plan, operator, telegram=True)
    elif state in ("delivered", "claimed"):
        _, item = delivered(buyer, plan, operator)
        if state == "claimed":
            claim(item, operator)
    else:
        _, item = queued(buyer, plan, operator)
        if state == "cancelled":
            cancel(item, operator)
    item.refresh_from_db()
    return item


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("reason", ["supply_failure", "input_timeout", "warranty_refund"])
def test_each_reason_cancels_only_from_its_own_statuses(buyer, plan, operator, state, reason):
    item = item_in(state, buyer, plan, operator)
    status, refunds = item.status, Refund.objects.count()
    with time_machine.travel(item.created_at + timedelta(days=22)):
        if (state, reason) in ALLOWED:
            cancel(item, operator, reason)
        else:
            with pytest.raises(ValidationError):
                cancel(item, operator, reason)
    item.refresh_from_db()
    if (state, reason) in ALLOWED:
        refund = Refund.objects.get(order_item=item)
        assert (item.status, item.cancel_reason, refund.amount) == ("CANCELLED", reason, 200000)
        event = item.events.latest("pk")
        assert (event.from_status, event.to_status, event.actor_user_id, event.note) == (
            status,
            "CANCELLED",
            operator.pk,
            REASON,
        )
    else:
        assert item.status == status and Refund.objects.count() == refunds


@pytest.mark.parametrize(
    "reason", ["customer_after_payment", "operator", "expired_unpaid", "customer_before_payment"]
)
def test_nothing_voluntary_cancels_a_paid_item(buyer, plan, operator, reason):
    _, item = queued(buyer, plan, operator)
    with pytest.raises(ValidationError, match="با این دلیل لغو"):
        cancel(item, operator, reason)
    item.refresh_from_db()
    assert item.status == "QUEUED" and not Refund.objects.exists()


def test_a_cancellation_needs_a_note(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    with pytest.raises(ValidationError, match="یادداشت را بنویسید"):
        cancel(item, operator, note="   ")
    assert not Refund.objects.exists()


# Missing details: 21 Tehran calendar days, and the unit goes back to stock


def test_missing_details_cancel_from_the_21st_tehran_day_after_the_request(buyer, plan, operator):
    with tehran(2026, 11, 3):
        _, item = queued(buyer, plan, operator, telegram=True)
    with time_machine.travel(datetime(2026, 11, 23, 23, 59, tzinfo=TEHRAN), tick=False):
        with pytest.raises(ValidationError, match="۲۱ روز"):
            cancel(item, operator, "input_timeout")
    with time_machine.travel(datetime(2026, 11, 24, 0, 1, tzinfo=TEHRAN), tick=False):
        cancel(item, operator, "input_timeout")
    item.refresh_from_db()
    assert (item.status, item.cancel_reason) == ("CANCELLED", "input_timeout")


@pytest.mark.parametrize("reason,stock", [("input_timeout", 5), ("supply_failure", 4)])
def test_only_a_missing_details_cancellation_returns_the_unit_to_stock(
    buyer, plan, operator, reason, stock
):
    plan.stock_quantity = 5
    _, item = queued(buyer, plan, operator, telegram=True)
    plan.refresh_from_db()
    assert plan.stock_quantity == 4
    with time_machine.travel(item.created_at + timedelta(days=22)):
        cancel(item, operator, reason)
    plan.refresh_from_db()
    assert plan.stock_quantity == stock


def test_a_warranty_refund_leaves_stock_alone(buyer, plan, operator):
    plan.stock_quantity = 5
    _, item = delivered(buyer, plan, operator)
    claim(item, operator)
    cancel(item, operator, "warranty_refund")
    plan.refresh_from_db()
    assert plan.stock_quantity == 4


# Warranty refunds: only inside the warranty, and for the unused days


@pytest.mark.parametrize(
    "warranty,claimed_on,allowed",
    [
        ("none", (2026, 11, 3), False),
        ("days_7", (2026, 11, 10), True),
        ("days_7", (2026, 11, 11), False),
        ("full_period", (2026, 12, 3), True),
        ("full_period", (2026, 12, 4), False),
    ],
)
def test_a_warranty_refund_needs_a_claim_opened_inside_the_warranty(
    buyer, plan, operator, warranty, claimed_on, allowed
):
    Product.objects.filter(pk=plan.product_id).update(warranty=warranty)
    with tehran(2026, 11, 3):
        _, item = queued(buyer, plan, operator)
        deliver(item, operator, expires_at=date(2026, 12, 3) if warranty == "full_period" else None)
    with tehran(*claimed_on):
        claim(item, operator)
        if allowed:
            cancel(item, operator, "warranty_refund")
        else:
            with pytest.raises(ValidationError, match="خارج از مدت گارانتی"):
                cancel(item, operator, "warranty_refund")
    item.refresh_from_db()
    assert item.status == ("CANCELLED" if allowed else "REPLACEMENT_REQUESTED")
    assert Refund.objects.filter(order_item=item).exists() is allowed


def test_a_seven_day_claim_after_the_subscription_ended_is_refused(buyer, plan, operator):
    with tehran(2026, 11, 3):
        _, item = queued(buyer, plan, operator)
        deliver(item, operator, expires_at=date(2026, 11, 5))
    with tehran(2026, 11, 6):
        claim(item, operator)
        with pytest.raises(ValidationError, match="خارج از مدت گارانتی"):
            cancel(item, operator, "warranty_refund")


def test_a_warranty_refund_pays_for_the_unused_days(buyer, plan, operator):
    Product.objects.filter(pk=plan.product_id).update(warranty="full_period")
    Plan.objects.filter(pk=plan.pk).update(sale_price=100000)
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=10000)
    # Delivered 22 November, last day 21 December: 30 days. The claim on 2 December
    # used 10 of them, and the days the claim stays open are not charged.
    with tehran(2026, 11, 22):
        _, items = paid_order(buyer, operator, [OrderLine(plan, 3)], "SAVE")
        deliver(items[1], operator, expires_at=date(2026, 12, 21))
    with tehran(2026, 12, 2):
        claim(items[1], operator)
    with tehran(2026, 12, 9):
        cancel(items[1], operator, "warranty_refund")
    assert items[1].discount_share == 3333
    assert Refund.objects.get().amount == 64445  # 96,667 × 20 ÷ 30, rounded half up


@pytest.mark.parametrize(
    "warranty,expires_at,claimed_on",
    [("full_period", date(2026, 12, 3), (2026, 11, 3)), ("days_7", None, (2026, 11, 6))],
)
def test_a_claim_on_the_delivery_day_or_without_an_end_date_refunds_in_full(
    buyer, plan, operator, warranty, expires_at, claimed_on
):
    Product.objects.filter(pk=plan.product_id).update(warranty=warranty)
    with tehran(2026, 11, 3):
        _, item = queued(buyer, plan, operator)
        deliver(item, operator, expires_at=expires_at)
    with tehran(*claimed_on):
        claim(item, operator)
        cancel(item, operator, "warranty_refund")
    assert Refund.objects.get().amount == 200000


# The discount use: held from placement, released when nothing it covered is delivered


def coded(buyer, plan, operator, quantity=1, other=None):
    code = DiscountCode.objects.create(
        code="SAVE", kind="fixed", value=10000, scope="selected", per_user_limit=1
    )
    code.products.add(plan.product)
    lines = [OrderLine(plan, quantity)] + ([OrderLine(other, 1)] if other else [])
    order, items = paid_order(buyer, operator, lines, "SAVE")
    return code, order, items


def code_use(code, order):
    code.refresh_from_db()
    return code.used_count, DiscountRedemption.objects.get(order=order).released_at is not None


def test_a_code_comes_back_when_its_only_covered_item_is_never_delivered(buyer, plan, operator):
    code, order, (item,) = coded(buyer, plan, operator)
    cancel(item, operator)
    assert code_use(code, order) == (0, True)
    again = services.place_order(user=buyer, lines=[OrderLine(plan, 1)], discount_code="SAVE")
    assert again.discount_amount == 10000


def test_a_code_stays_spent_once_a_covered_item_was_delivered(buyer, plan, operator):
    code, order, items = coded(buyer, plan, operator, quantity=2)
    deliver(items[0], operator)
    cancel(items[1], operator)
    assert code_use(code, order) == (1, False)


def test_a_warranty_refund_keeps_the_code_spent(buyer, plan, operator):
    code, order, (item,) = coded(buyer, plan, operator)
    deliver(item, operator)
    claim(item, operator)
    cancel(item, operator, "warranty_refund")
    assert code_use(code, order) == (1, False)


def test_a_code_comes_back_only_when_its_last_covered_item_ends_undelivered(buyer, plan, operator):
    code, order, items = coded(buyer, plan, operator, quantity=2)
    cancel(items[0], operator)
    assert code_use(code, order) == (1, False)
    cancel(items[1], operator)
    assert code_use(code, order) == (0, True)


@pytest.mark.parametrize("first", ["covered", "uncovered"])
def test_an_uncovered_item_never_moves_the_code_and_a_use_is_released_once(
    buyer, plan, operator, first
):
    code, order, (covered, uncovered) = coded(buyer, plan, operator, other=separate_plan(plan))
    assert (covered.discount_share, uncovered.discount_share) == (10000, 0)
    if first == "uncovered":
        cancel(uncovered, operator)
        assert code_use(code, order) == (1, False)
        cancel(covered, operator)
    else:
        cancel(covered, operator)
        assert code_use(code, order) == (0, True)
        released = DiscountRedemption.objects.get(order=order).released_at
        cancel(uncovered, operator)
        assert DiscountRedemption.objects.get(order=order).released_at == released
    assert code_use(code, order) == (0, True)


def test_an_item_a_fixed_code_paid_for_in_full_cancels_with_nothing_owed(buyer, plan, operator):
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=300000, scope="selected")
    code.products.add(plan.product)
    order, (free, paid) = paid_order(
        buyer, operator, [OrderLine(plan, 1), OrderLine(separate_plan(plan), 1)], "SAVE"
    )
    assert (free.paid_amount, paid.paid_amount, order.total_amount) == (0, 200000, 200000)
    cancel(free, operator)
    free.refresh_from_db()
    assert free.status == "CANCELLED" and not Refund.objects.exists()
    assert code_use(code, order) == (0, True)


# Concurrent work on the same rows waits its turn


def pause_first_amount(monkeypatch):
    """Holds the first cancellation after it has taken every lock, until released."""
    entered, release = Event(), Event()
    original = services.refund_amount

    def paused(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(services, "refund_amount", paused)
    return entered, release


def test_a_checkout_waits_for_a_cancellation_that_frees_a_single_use_code(
    buyer, plan, operator, monkeypatch
):
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=10000, max_uses=1)
    _, (item,) = paid_order(buyer, operator, [OrderLine(plan, 1)], "SAVE")
    second = User.objects.create_user("second@example.test")
    entered, release = pause_first_amount(monkeypatch)
    pids: Queue = Queue()
    with ThreadPoolExecutor(2) as pool:
        cancelling = pool.submit(in_connection, pids, lambda: cancel(item, operator))
        assert entered.wait(10)
        holder = pids.get(timeout=5)
        checkout = pool.submit(
            in_connection,
            pids,
            lambda: services.place_order(
                user=second, lines=[OrderLine(plan, 1)], discount_code="SAVE"
            ),
        )
        wait_for_lock(pids.get(timeout=5), holder)
        release.set()
        cancelling.result(timeout=15)
        placed = checkout.result(timeout=15)
    assert placed is not None and placed.discount_amount == 10000
    code.refresh_from_db()
    assert code.used_count == 1


def test_two_cancellations_on_one_order_take_turns_and_release_the_code_once(
    buyer, plan, operator, monkeypatch
):
    code, order, (first, second) = coded(buyer, plan, operator, quantity=2)
    entered, release = pause_first_amount(monkeypatch)
    pids: Queue = Queue()
    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(in_connection, pids, lambda: cancel(first, operator))
        assert entered.wait(10)
        holder = pids.get(timeout=5)
        two = pool.submit(in_connection, pids, lambda: cancel(second, operator))
        wait_for_lock(pids.get(timeout=5), holder)
        release.set()
        assert one.result(timeout=15) is not None and two.result(timeout=15) is not None
    assert Refund.objects.count() == 2
    assert code_use(code, order) == (0, True)


# Whole or nothing, once, on its own order, by the right person


def test_a_failure_after_the_refund_rolls_the_whole_cancellation_back(buyer, plan, operator):
    code, order, (item,) = coded(buyer, plan, operator)
    with (
        patch("apps.orders.services._release_redemption", side_effect=DatabaseError),
        pytest.raises(DatabaseError),
    ):
        cancel(item, operator)
    item.refresh_from_db()
    assert (item.status, item.cancel_reason) == ("QUEUED", None)
    assert not Refund.objects.exists()
    assert not item.events.filter(to_status="CANCELLED").exists()
    assert code_use(code, order) == (1, False)


def test_a_repeated_or_stale_cancellation_changes_nothing(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    page = event_id(item)
    cancel(item, operator, expected_event_id=page)
    with pytest.raises(ValidationError, match="قبلاً ثبت شده"):
        cancel(item, operator, expected_event_id=page)
    second = User.objects.create_superuser("second@example.test")
    with pytest.raises(ValidationError, match="صفحه را دوباره باز کنید"):
        cancel(item, second, expected_event_id=page)
    assert Refund.objects.count() == 1
    assert item.events.filter(to_status="CANCELLED").count() == 1


def test_an_item_is_cancelled_only_through_its_own_order(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    other, _ = queued(User.objects.create_user("other@example.test"), plan, operator)
    with pytest.raises(PermissionDenied):
        services.cancel_paid_item(
            order_id=other.pk,
            item_id=item.pk,
            actor=operator,
            expected_event_id=event_id(item),
            reason="supply_failure",
            note=REASON,
        )
    assert not Refund.objects.exists()


def test_the_share_migration_fills_existing_orders_and_reverses(buyer, plan):
    previous = ("orders", "0018_delivery")
    other = separate_plan(plan)
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        old = executor.loader.project_state([previous]).apps
        Order, Item = old.get_model("orders", "Order"), old.get_model("orders", "OrderItem")

        def placed(prices, discount, snapshot):
            subtotal = sum(price for _, price in prices)
            order = Order.objects.create(
                user_id=buyer.pk,
                subtotal=subtotal,
                discount_amount=discount,
                total_amount=subtotal - discount,
                discount_snapshot=snapshot,
            )
            return [
                Item.objects.create(
                    order=order,
                    plan_id=plan_id,
                    price_snapshot=price,
                    cost_snapshot=0,
                    product_snapshot={},
                ).pk
                for plan_id, price in prices
            ]

        everything = placed([(plan.pk, 100000)] * 3, 10000, {"scope": "all"})
        selected = placed(
            [(plan.pk, 300000), (other.pk, 100000)],
            30000,
            {"scope": "selected", "products": [plan.product_id]},
        )
        none = placed([(plan.pk, 100000)], 0, {})
        free = placed([(plan.pk, 0), (plan.pk, 30000), (plan.pk, 30000)], 10001, {"scope": "all"})
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from apps.orders.models import OrderItem

        def shares(ids):
            return [OrderItem.objects.get(pk=pk).discount_share for pk in ids]

        assert shares(everything) == [3334, 3333, 3333]
        assert shares(selected) == [30000, 0]
        assert shares(none) == [0]
        assert shares(free) == [0, 5001, 5000]
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        assert Item.objects.filter(pk__in=everything + selected + none + free).count() == 9
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_only_staff_who_may_record_refunds_cancel_a_paid_item(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    clerk = User.objects.create_user("clerk@example.test", is_staff=True)
    clerk.user_permissions.add(
        *Permission.objects.filter(codename__in=["change_order", "change_orderitem"])
    )
    for actor in (buyer, User.objects.get(pk=clerk.pk)):
        with pytest.raises(PermissionDenied):
            cancel(item, actor)
    clerk.user_permissions.add(Permission.objects.get(codename="add_refund"))
    cancel(item, User.objects.get(pk=clerk.pk))
    assert Refund.objects.get().created_by_id == clerk.pk

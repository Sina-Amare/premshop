from datetime import timedelta

import pytest
import time_machine
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.orders.models import Order, OrderItem
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db(transaction=True)


def test_tasks_are_explicitly_asynchronous_with_one_redis_consumer():
    assert settings.HUEY["immediate"] is False
    assert settings.HUEY["huey_class"] == "huey.RedisHuey"
    assert settings.HUEY["consumer"]["workers"] == 1


def test_queue_identity_separates_databases_without_using_credentials():
    from config.queue import queue_name

    original = {"NAME": "shop", "HOST": "db", "PORT": "5432", "PASSWORD": "first"}
    assert queue_name(original) == queue_name({**original, "PASSWORD": "second"})
    for key in ("NAME", "HOST", "PORT"):
        assert queue_name(original) != queue_name({**original, key: "another"})
    assert "first" not in queue_name(original)


def test_worker_restart_catches_up_and_duplicate_sweeps_are_harmless(buyer, plan):
    from huey.contrib.djhuey import HUEY
    from huey.storage import MemoryStorage

    from apps.orders.models import UnpaidExpirySweep
    from apps.orders.tasks import sweep_unpaid_orders

    assert isinstance(HUEY.storage, MemoryStorage)
    assert HUEY.immediate is False
    with time_machine.travel("2026-10-29 08:00:00", tick=False) as clock:
        order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
        sweep_unpaid_orders()
        assert order.items.get().status == OrderItem.Status.PENDING_PAYMENT
        clock.shift(25 * 3600)
        assert order.unpaid_expires_at < timezone.now()
        HUEY.execute(HUEY.dequeue())
        assert order.items.get().status == OrderItem.Status.CANCELLED
        sweep_unpaid_orders()
        HUEY.execute(HUEY.dequeue())
        assert order.items.get().events.count() == 2
        state = UnpaidExpirySweep.objects.get(pk=1)
        assert state.last_success_at == timezone.now()
        assert state.overdue_count == 0
        assert timezone.now() - order.created_at == timedelta(hours=25)


def test_daily_cleanup_uses_the_queue_and_keeps_live_sessions_and_account_carts(buyer):
    from huey.contrib.djhuey import HUEY

    from apps.cart.models import Cart
    from apps.cart.tasks import clean_guest_carts
    from apps.core.tasks import clean_expired_sessions

    expired = Cart.objects.create(session_key="expired")
    recent = Cart.objects.create(session_key="recent")
    owned = Cart.objects.create(user=buyer)
    Cart.objects.filter(pk__in=[expired.pk, owned.pk]).update(
        updated_at=timezone.now() - timedelta(days=31)
    )
    Session.objects.create(
        session_key="expired", session_data="", expire_date=timezone.now() - timedelta(seconds=1)
    )
    Session.objects.create(
        session_key="recent", session_data="", expire_date=timezone.now() + timedelta(days=1)
    )
    clean_guest_carts()
    clean_expired_sessions()
    assert Cart.objects.filter(pk=expired.pk).exists() and Session.objects.count() == 2
    HUEY.execute(HUEY.dequeue())
    HUEY.execute(HUEY.dequeue())
    assert set(Cart.objects.values_list("pk", flat=True)) == {recent.pk, owned.pk}
    assert list(Session.objects.values_list("session_key", flat=True)) == ["recent"]


def test_expiry_batches_catch_up_and_a_bad_order_does_not_block_later_orders(
    buyer, plan, monkeypatch, caplog
):
    from apps.accounts.models import User
    from apps.orders import services

    orders = [place_order(user=buyer, lines=[OrderLine(plan, 1)])]
    for number in range(2):
        user = User.objects.create_user(f"other{number}@example.test")
        orders.append(place_order(user=user, lines=[OrderLine(plan, 1)]))
    Order.objects.update(unpaid_expires_at=timezone.now())
    cancel = services._cancel

    def skip_bad_order(**kwargs):
        if kwargs["order_id"] == orders[0].pk:
            raise ValidationError("private-customer-input")
        return cancel(**kwargs)

    monkeypatch.setattr(services, "_cancel", skip_bad_order)
    assert services.expire_unpaid_orders(limit=2) == 1
    assert orders[2].items.get().status == OrderItem.Status.PENDING_PAYMENT
    assert services.expire_unpaid_orders(limit=2) == 1
    assert orders[0].items.get().status == OrderItem.Status.PENDING_PAYMENT
    assert "private-customer-input" not in caplog.text
    monkeypatch.setattr(services, "_cancel", cancel)
    assert services.expire_unpaid_orders(limit=2) == 1
    assert services.expire_unpaid_orders(limit=2) == 0


def test_a_full_failed_batch_does_not_starve_later_orders_or_skip_failed_retries(
    buyer, plan, monkeypatch
):
    from apps.accounts.models import User
    from apps.orders import services

    first = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    other = User.objects.create_user("other@example.test")
    second = place_order(user=other, lines=[OrderLine(plan, 1)])
    Order.objects.update(unpaid_expires_at=timezone.now())
    cancel = services._cancel
    attempts = []

    def skip_bad_order(**kwargs):
        attempts.append(kwargs["order_id"])
        if kwargs["order_id"] == first.pk:
            raise ValidationError("needs consistency review")
        return cancel(**kwargs)

    monkeypatch.setattr(services, "_cancel", skip_bad_order)
    assert services.expire_unpaid_orders(limit=1) == 0
    assert services.expire_unpaid_orders(limit=1) == 1
    assert second.items.get().status == OrderItem.Status.CANCELLED
    assert services.expire_unpaid_orders(limit=1) == 0
    assert attempts == [first.pk, second.pk, first.pk]
    monkeypatch.setattr(services, "_cancel", cancel)
    assert services.expire_unpaid_orders(limit=1) == 1


def test_failed_sweep_does_not_claim_success_or_hide_the_backlog(buyer, plan, monkeypatch):
    from huey.contrib.djhuey import HUEY

    from apps.orders import tasks
    from apps.orders.models import UnpaidExpirySweep

    order = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    Order.objects.filter(pk=order.pk).update(unpaid_expires_at=timezone.now())
    monkeypatch.setattr(tasks, "expire_unpaid_orders", lambda: 0)
    tasks.sweep_unpaid_orders()
    HUEY.execute(HUEY.dequeue())
    state = UnpaidExpirySweep.objects.get()
    assert state.overdue_count == 1
    success = state.last_success_at

    def failure():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(tasks, "expire_unpaid_orders", failure)
    tasks.sweep_unpaid_orders()
    HUEY.execute(HUEY.dequeue())
    state.refresh_from_db()
    assert state.last_success_at == success and state.overdue_count == 1

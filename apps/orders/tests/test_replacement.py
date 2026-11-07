from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.core.formatting import format_jalali, to_persian_digits
from apps.orders import models, services
from apps.orders.clock import TEHRAN
from apps.orders.tests.test_delivery import (
    VALUE,
    deliver,
    delivery_url,
    form_data,
    queued,
)
from apps.orders.tests.test_input_clock import act, event_id
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = pytest.mark.django_db(transaction=True)

CLAIM = "حساب مشتری مسدود شد"
REJECTION = "مسدودی به خاطر استفاده‌ی خود مشتری بود"
DELAY = "تأمین‌کننده تا فردا عصر کد می‌فرستد"
NEW_VALUE = "replacement-secret-3J8"


def delivered(buyer, plan, operator, **changes):
    order, item = queued(buyer, plan, operator)
    deliver(item, operator, **changes)
    item.refresh_from_db()
    return order, item


def claim(item, actor, **changes):
    return act(services.open_claim, item, actor, note=changes.pop("note", CLAIM), **changes)


def reject(item, actor, **changes):
    return act(services.reject_claim, item, actor, note=changes.pop("note", REJECTION), **changes)


def replace(item, actor, **changes):
    return services.replace_delivery(
        order_id=item.order_id,
        item_id=item.pk,
        actor=actor,
        expected_event_id=changes.pop("expected_event_id", event_id(item)),
        lines=changes.pop("lines", [services.DeliveryLine("رمز عبور", NEW_VALUE)]),
        note=changes.pop("note", ""),
        expires_at=changes.pop("expires_at", None),
        extra_cost=changes.pop("extra_cost", Decimal("0")),
    )


def delay(item, actor, **changes):
    return act(
        services.delay_supply,
        item,
        actor,
        hours=changes.pop("hours", 24),
        note=changes.pop("note", DELAY),
        **changes,
    )


def current_values(item, operator):
    content = services.reveal_delivery(
        order_id=item.order_id, item_id=item.pk, actor=operator, ip=None
    )
    return [line.value for line in content.lines]


def stored_rows():
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, value FROM orders_deliveryfield ORDER BY id")
        fields = cursor.fetchall()
        cursor.execute("SELECT id, note FROM orders_delivery ORDER BY id")
        return fields, cursor.fetchall()


def at_tehran(*args):
    return datetime(*args, tzinfo=TEHRAN)


# The claim loop


def test_a_claim_opens_with_its_note_and_one_history_row(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    before = item.events.count()
    claim(item, operator, note=f"  {CLAIM}  ")
    item.refresh_from_db()
    event = item.events.latest("pk")
    assert item.status == "REPLACEMENT_REQUESTED" and item.events.count() == before + 1
    assert (event.from_status, event.to_status, event.actor, event.actor_user_id, event.note) == (
        "DELIVERED",
        "REPLACEMENT_REQUESTED",
        "operator",
        operator.pk,
        CLAIM,
    )
    assert models.Delivery.objects.count() == 1


def test_three_replacement_rounds_keep_four_sets_and_the_latest_is_current(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator, actual_cost=Decimal("100000"))
    first_delivery = item.delivered_at
    for n in range(1, 4):
        claim(item, operator)
        expires = timezone.localdate() + timedelta(days=30 + n)
        delivery = replace(
            item,
            operator,
            lines=[services.DeliveryLine("رمز عبور", f"round-{n}-secret")],
            expires_at=expires,
            extra_cost=Decimal("10000"),
        )
        item.refresh_from_db()
        event = delivery.event
        assert (event.from_status, event.to_status, event.actor_user_id) == (
            "REPLACEMENT_REQUESTED",
            "DELIVERED",
            operator.pk,
        )
        assert item.status == "DELIVERED" and item.expires_at == expires
    assert models.Delivery.objects.filter(order_item=item).count() == 4
    assert item.delivered_at == first_delivery
    assert item.actual_cost == Decimal("130000")
    assert current_values(item, operator) == ["round-3-secret"]


def test_a_rejected_claim_writes_no_set_and_leaves_the_current_one(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator, actual_cost=Decimal("100000"))
    rows = stored_rows()
    claim(item, operator)
    reject(item, operator)
    item.refresh_from_db()
    event = item.events.latest("pk")
    assert item.status == "DELIVERED" and item.actual_cost == Decimal("100000")
    assert (event.from_status, event.to_status, event.note) == (
        "REPLACEMENT_REQUESTED",
        "DELIVERED",
        REJECTION,
    )
    assert not hasattr(event, "delivery")
    assert stored_rows() == rows
    assert current_values(item, operator) == [VALUE]


def test_deliver_item_never_serves_an_open_claim(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    claim(item, operator)
    before = item.events.count()
    with pytest.raises(ValidationError):
        deliver(item, operator)
    item.refresh_from_db()
    assert item.status == "REPLACEMENT_REQUESTED" and item.events.count() == before
    assert models.Delivery.objects.count() == 1


CLAIM_REFUSALS = {
    "customer": PermissionDenied,
    "unpermitted_staff": PermissionDenied,
    "other_order": PermissionDenied,
    "stale_page": ValidationError,
    "blank_note": ValidationError,
    "long_note": ValidationError,
    "not_delivered": ValidationError,
    "already_open": ValidationError,
}


@pytest.mark.parametrize("case", CLAIM_REFUSALS)
def test_refused_claims_change_nothing(buyer, plan, operator, case):
    if case == "not_delivered":
        _, item = queued(buyer, plan, operator)
    else:
        _, item = delivered(buyer, plan, operator)
    if case == "already_open":
        claim(item, operator)
    actor, changes = operator, {}
    if case == "customer":
        actor = buyer
    elif case == "unpermitted_staff":
        actor = User.objects.create_user("staff@example.test", is_staff=True)
    elif case == "other_order":
        changes["order_id"] = delivered(User.objects.create_user("o@example.test"), plan, operator)[
            0
        ].pk
    elif case == "stale_page":
        changes["expected_event_id"] = event_id(item) - 1
    elif case == "blank_note":
        changes["note"] = "   "
    elif case == "long_note":
        changes["note"] = "ب" * 2001
    item.refresh_from_db()
    status, before = item.status, item.events.count()
    with pytest.raises(CLAIM_REFUSALS[case]):
        if "order_id" in changes:
            services.open_claim(
                order_id=changes["order_id"],
                item_id=item.pk,
                actor=actor,
                expected_event_id=event_id(item),
                note=CLAIM,
            )
        else:
            claim(item, actor, **changes)
    item.refresh_from_db()
    assert item.status == status and item.events.count() == before


REPLACE_REFUSALS = {
    "customer": PermissionDenied,
    "unpermitted_staff": PermissionDenied,
    "stale_page": ValidationError,
    "no_claim": ValidationError,
    "no_lines": ValidationError,
    "past_expiry": ValidationError,
    "negative_extra": ValidationError,
    "fractional_extra": ValidationError,
    "past_maximum": ValidationError,
}


@pytest.mark.parametrize("case", REPLACE_REFUSALS)
def test_refused_replacements_change_nothing(buyer, plan, operator, case):
    _, item = delivered(buyer, plan, operator, actual_cost=Decimal("100000"))
    if case != "no_claim":
        claim(item, operator)
    actor, changes = operator, {}
    if case == "customer":
        actor = buyer
    elif case == "unpermitted_staff":
        actor = User.objects.create_user("staff@example.test", is_staff=True)
    elif case == "stale_page":
        changes["expected_event_id"] = event_id(item) - 1
    elif case == "no_lines":
        changes["lines"] = []
    elif case == "past_expiry":
        changes["expires_at"] = timezone.localdate() - timedelta(days=1)
    elif case == "negative_extra":
        changes["extra_cost"] = Decimal("-1")
    elif case == "fractional_extra":
        changes["extra_cost"] = Decimal("0.5")
    elif case == "past_maximum":
        changes["extra_cost"] = services.MAX_AMOUNT
    item.refresh_from_db()
    status, before, rows = item.status, item.events.count(), stored_rows()
    with pytest.raises(REPLACE_REFUSALS[case]):
        replace(item, actor, **changes)
    item.refresh_from_db()
    assert item.status == status and item.events.count() == before
    assert item.actual_cost == Decimal("100000") and stored_rows() == rows


REJECT_REFUSALS = {
    "customer": PermissionDenied,
    "stale_page": ValidationError,
    "no_claim": ValidationError,
    "blank_note": ValidationError,
}


@pytest.mark.parametrize("case", REJECT_REFUSALS)
def test_refused_rejections_change_nothing(buyer, plan, operator, case):
    _, item = delivered(buyer, plan, operator)
    if case != "no_claim":
        claim(item, operator)
    actor, changes = operator, {}
    if case == "customer":
        actor = buyer
    elif case == "stale_page":
        changes["expected_event_id"] = event_id(item) - 1
    elif case == "blank_note":
        changes["note"] = ""
    item.refresh_from_db()
    status, before = item.status, item.events.count()
    with pytest.raises(REJECT_REFUSALS[case]):
        reject(item, actor, **changes)
    item.refresh_from_db()
    assert item.status == status and item.events.count() == before


@pytest.mark.parametrize("kind", ["claim", "replace", "reject", "delay"])
def test_a_repeated_submission_is_refused_and_stores_nothing(buyer, plan, operator, kind):
    if kind == "delay":
        _, item = queued(buyer, plan, operator)
    else:
        _, item = delivered(buyer, plan, operator)
    if kind in ("replace", "reject"):
        claim(item, operator)
    run = {"claim": claim, "replace": replace, "reject": reject, "delay": delay}[kind]
    page = event_id(item)
    run(item, operator, expected_event_id=page)
    item.refresh_from_db()
    state, before, rows = (item.status, item.due_at), item.events.count(), stored_rows()
    with pytest.raises(ValidationError) as refused:
        run(item, operator, expected_event_id=page)
    assert "قبلاً" in " ".join(refused.value.messages)
    item.refresh_from_db()
    assert (item.status, item.due_at) == state and item.events.count() == before
    assert stored_rows() == rows


def test_a_replacement_page_from_before_a_rejection_gets_the_stale_refusal(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    claim(item, operator)
    page = event_id(item)
    reject(item, operator, expected_event_id=page)
    with pytest.raises(ValidationError) as refused:
        replace(item, operator, expected_event_id=page)
    assert "صفحه را دوباره باز کنید" in " ".join(refused.value.messages)
    assert models.Delivery.objects.count() == 1


def test_a_full_period_warranty_needs_an_end_date_to_deliver_or_replace(buyer, plan, operator):
    Product.objects.filter(pk=plan.product_id).update(warranty=Product.Warranty.FULL_PERIOD)
    plan.product.refresh_from_db()
    _, item = queued(buyer, plan, operator)
    with pytest.raises(ValidationError) as refused:
        deliver(item, operator, expires_at=None)
    assert "تاریخ پایان اشتراک" in " ".join(refused.value.messages)
    deliver(item, operator, expires_at=timezone.localdate() + timedelta(days=30))
    claim(item, operator)
    with pytest.raises(ValidationError):
        replace(item, operator, expires_at=None)
    assert models.Delivery.objects.count() == 1


# Supply delay


@pytest.mark.parametrize(
    ("due", "hours", "moved"),
    [
        (at_tehran(2026, 12, 1, 12, 0), 3, at_tehran(2026, 12, 1, 15, 0)),
        (at_tehran(2026, 12, 1, 20, 0), 3, at_tehran(2026, 12, 2, 10, 0)),
        (at_tehran(2026, 12, 1, 12, 0), 72, at_tehran(2026, 12, 4, 12, 0)),
    ],
)
def test_a_supply_delay_moves_the_deadline_later_with_an_annotation(
    buyer, plan, operator, due, hours, moved
):
    _, item = queued(buyer, plan, operator)
    models.OrderItem.objects.filter(pk=item.pk).update(due_at=due)
    before = item.events.count()
    delay(item, operator, hours=hours)
    item.refresh_from_db()
    event = item.events.latest("pk")
    assert item.status == "QUEUED" and item.due_at == moved
    assert item.events.count() == before + 1
    assert (event.from_status, event.to_status, event.actor_user_id) == (
        "QUEUED",
        "QUEUED",
        operator.pk,
    )
    assert DELAY in event.note and format_jalali(moved) in event.note


DELAY_REFUSALS = {
    "customer": PermissionDenied,
    "stale_page": ValidationError,
    "delivered": ValidationError,
    "paused": ValidationError,
    "no_deadline": ValidationError,
    "zero_hours": ValidationError,
    "too_many_hours": ValidationError,
    "text_hours": ValidationError,
    "blank_note": ValidationError,
}


@pytest.mark.parametrize("case", DELAY_REFUSALS)
def test_refused_delays_change_nothing(buyer, plan, operator, case):
    if case == "delivered":
        _, item = delivered(buyer, plan, operator)
    else:
        _, item = queued(buyer, plan, operator)
    actor, changes = operator, {}
    if case == "customer":
        actor = buyer
    elif case == "stale_page":
        changes["expected_event_id"] = event_id(item) - 1
    elif case == "paused":
        models.OrderItem.objects.filter(pk=item.pk).update(sla_paused_at=timezone.now())
    elif case == "no_deadline":
        models.OrderItem.objects.filter(pk=item.pk).update(due_at=None)
    elif case == "zero_hours":
        changes["hours"] = 0
    elif case == "too_many_hours":
        changes["hours"] = 73
    elif case == "text_hours":
        changes["hours"] = "24"
    elif case == "blank_note":
        changes["note"] = " "
    item.refresh_from_db()
    state, before = (item.status, item.due_at), item.events.count()
    with pytest.raises(DELAY_REFUSALS[case]):
        delay(item, actor, **changes)
    item.refresh_from_db()
    assert (item.status, item.due_at) == state and item.events.count() == before


# The panel


def item_action_url(item, action):
    return reverse("admin:orders_order_item_action", args=[item.order_id, item.pk, action])


def test_order_page_offers_a_claim_for_a_delivered_item(client, buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    client.force_login(operator)
    body = client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
    assert f'href="{delivery_url(item)}"' in body and "درخواست تعویض" in body


def test_operator_opens_a_claim_and_replaces_through_the_panel(client, buyer, plan, operator):
    order, item = delivered(buyer, plan, operator, actual_cost=Decimal("100000"))
    client.force_login(operator)
    page = client.get(delivery_url(item)).content.decode()
    assert f'action="{item_action_url(item, "claim")}"' in page
    response = client.post(
        item_action_url(item, "claim"), {"expected_event_id": event_id(item), "note": CLAIM}
    )
    assert response.status_code == 302 and response["Location"] == delivery_url(item)
    page = client.get(delivery_url(item)).content.decode()
    assert CLAIM in page and "هزینه‌ی تأمین اضافه" in page
    assert f'action="{item_action_url(item, "reject")}"' in page
    preview = client.post(
        delivery_url(item), form_data(item, "preview", actual_cost=to_persian_digits(15000))
    )
    assert preview.status_code == 200 and "data-delivery-card" in preview.content.decode()
    response = client.post(
        delivery_url(item), form_data(item, "confirm", actual_cost=to_persian_digits(15000))
    )
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.actual_cost == Decimal("115000")
    assert models.Delivery.objects.filter(order_item=item).count() == 2


def test_operator_rejects_a_claim_through_the_panel(client, buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    claim(item, operator)
    client.force_login(operator)
    response = client.post(
        item_action_url(item, "reject"), {"expected_event_id": event_id(item), "note": REJECTION}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("admin:orders_order_change", args=[order.pk])
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.events.latest("pk").note == REJECTION


def test_operator_records_a_supply_delay_through_the_panel(client, buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    due = item.due_at
    client.force_login(operator)
    page = client.get(delivery_url(item)).content.decode()
    assert f'action="{item_action_url(item, "delay")}"' in page
    response = client.post(
        item_action_url(item, "delay"),
        {"expected_event_id": event_id(item), "note": DELAY, "hours": "۲۴"},
    )
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.due_at > due and item.events.latest("pk").note.startswith(DELAY)


@pytest.mark.parametrize(
    ("action", "data"),
    [
        ("claim", {"note": ""}),
        ("delay", {"note": DELAY, "hours": "یک"}),
        ("unknown", {"note": CLAIM}),
    ],
)
def test_a_refused_panel_action_changes_nothing(client, buyer, plan, operator, action, data):
    _, item = (
        delivered(buyer, plan, operator) if action != "delay" else queued(buyer, plan, operator)
    )
    client.force_login(operator)
    before = item.events.count()
    response = client.post(
        item_action_url(item, action), {"expected_event_id": event_id(item), **data}
    )
    assert response.status_code == (404 if action == "unknown" else 400)
    if action != "unknown":
        assert 'role="alert"' in response.content.decode()
    assert item.events.count() == before


def test_item_actions_need_post_and_change_permission(client, buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    staff = User.objects.create_user("viewer@example.test", is_staff=True)
    client.force_login(staff)
    data = {"expected_event_id": event_id(item), "note": CLAIM}
    assert client.post(item_action_url(item, "claim"), data).status_code == 403
    client.force_login(operator)
    assert client.get(item_action_url(item, "claim")).status_code == 405
    item.refresh_from_db()
    assert item.status == "DELIVERED"


def test_open_claims_have_their_own_queue_tab_with_a_count(client, buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    claim(item, operator)
    client.force_login(operator)
    listing = reverse("admin:orders_order_changelist")
    response = client.get(listing, {"tab": "claims"})
    assert list(response.context["cl"].result_list) == [order]
    assert response.context["queue_counts"]["claims"] == 1
    assert "درخواست تعویض" in response.content.decode()
    assert list(client.get(listing).context["cl"].result_list) == []

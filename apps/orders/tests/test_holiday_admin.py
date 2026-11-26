"""The holiday in the panel: its page, its banner, and the queue and delivery pages during one."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.orders import holiday, services
from apps.orders.admin import queue_overview
from apps.orders.models import Holiday, OrderItem
from apps.orders.services import OrderLine, place_order
from apps.orders.tests.test_delivery import delivery_url, form_data, queued
from apps.orders.tests.test_input_clock import act, event_id, purchase
from apps.orders.tests.test_paid_cancellation import cancel, paid_order

pytestmark = pytest.mark.django_db

STALE = "وضعیت محصول تغییر کرده است"


@pytest.fixture
def owner(client):
    user = User.objects.create_superuser("owner@example.test")
    client.force_login(user)
    return user


def page():
    return reverse("admin:orders_order_holiday")


def queue(tab="queued"):
    return reverse("admin:orders_order_changelist") + f"?tab={tab}"


def begin(owner):
    latest = Holiday.objects.order_by("-pk").values_list("pk", flat=True).first()
    return holiday.start(actor=owner, expected_latest=latest)


def finish(owner):
    return holiday.end(actor=owner, expected_open=Holiday.objects.get(ended_at=None).pk)


def staff(*codenames):
    user = User.objects.create_user("staff@example.test", is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return User.objects.get(pk=user.pk)


def make_order(buyer, plan, quantity=1, **times):
    order = place_order(user=buyer, lines=[OrderLine(plan, quantity)])
    OrderItem.objects.filter(order=order).update(status="QUEUED", **times)
    return order


# The page


def test_the_page_says_what_starting_would_do_then_asks_once(client, owner, buyer, plan):
    due = timezone.now() + timedelta(hours=3)
    make_order(buyer, plan, quantity=2, due_at=due)
    waiting = make_order(buyer, plan, due_at=due)
    OrderItem.objects.filter(order=waiting).update(
        status="AWAITING_INPUT", sla_paused_at=timezone.now(), input_requested_at=timezone.now()
    )
    body = client.get(page()).content.decode()
    assert "تحویل طبق روال" in body and 'data-tone="done"' in body
    assert "۲ محصول در ۱ سفارش" in body
    assert "۱ محصول" in body  # the one waiting for details, which a holiday leaves alone
    review = client.post(page(), {"action": "start", "step": "review", "expected": ""})
    assert review.status_code == 200 and not Holiday.objects.exists()
    review_body = review.content.decode()
    assert 'name="step" value="confirm"' in review_body and "۲ محصول" in review_body
    done = client.post(page(), {"action": "start", "step": "confirm", "expected": ""})
    assert done.status_code == 302 and done.url == page()
    opened = Holiday.objects.get(ended_at=None)
    assert opened.started_by == owner
    body = client.get(page()).content.decode()
    assert 'data-icon="pause"' in body and "پایان تعطیلی" in body
    assert "۲ محصول در ۱ سفارش" in body


def test_ending_from_the_page_moves_the_deadlines_and_lists_the_holiday(client, owner, buyer, plan):
    order = make_order(buyer, plan, due_at=timezone.now() + timedelta(hours=3))
    opened = begin(owner)
    body = client.get(page()).content.decode()
    assert "تخمین" in body  # the new deadline is an estimate until the end is pressed
    review = client.post(page(), {"action": "end", "step": "review", "expected": opened.pk})
    review_body = review.content.decode()
    assert 'name="step" value="confirm"' in review_body
    assert "از همین حالا مهلت ۱ محصول در صف دوباره می‌گذرد." in review_body
    done = client.post(page(), {"action": "end", "step": "confirm", "expected": opened.pk})
    assert done.status_code == 302
    opened.refresh_from_db()
    assert opened.ended_at is not None and opened.ended_by == owner
    assert order.items.get().sla_paused_at is None
    body = client.get(page()).content.decode()
    assert "تحویل طبق روال" in body and "تعطیلی‌های گذشته" in body and owner.email in body
    assert "تعطیلات تمام شد و برگشتیم." in body
    assert "مهلت جدید هر محصول را در صف سفارش‌ها می‌بینید." in body


def test_a_stale_form_is_explained_on_the_page_and_changes_nothing(client, owner):
    first = begin(owner)
    finish(owner)
    begin(owner)
    response = client.post(page(), {"action": "end", "step": "confirm", "expected": first.pk})
    body = response.content.decode()
    assert response.status_code == 400 and 'role="alert"' in body
    assert "تعطیلی دیگری شروع شده است." in body
    assert "تعطیلات تمام شد و برگشتیم." not in body
    assert Holiday.objects.filter(ended_at=None).count() == 1


def test_a_stale_end_form_says_the_shop_has_reopened(client, owner):
    opened = begin(owner)
    finish(owner)
    response = client.post(page(), {"action": "end", "step": "confirm", "expected": opened.pk})
    assert response.status_code == 400
    assert "تعطیلات تمام شد و برگشتیم." in response.content.decode()
    assert Holiday.objects.count() == 1 and not Holiday.objects.filter(ended_at=None).exists()


@pytest.mark.parametrize("value", [None, "bad", "²"])
def test_first_start_requires_an_explicit_blank_token(client, owner, value):
    data = {"action": "start", "step": "confirm"}
    if value is not None:
        data["expected"] = value
    response = client.post(page(), data)
    assert response.status_code == 400
    assert "صفحه را دوباره باز کنید" in response.content.decode()
    assert not Holiday.objects.exists()


def test_the_history_has_an_empty_state(client, owner):
    assert "هنوز تعطیلی‌ای ثبت نشده است." in client.get(page()).content.decode()


def test_holiday_copy_describes_clocks_starting_from_payment_or_details(client, owner, buyer, plan):
    review = client.post(page(), {"action": "start", "step": "review", "expected": ""})
    assert "تا پایان تعطیلی سفارش جدید نمی‌گیریم." in review.content.decode()
    _, waiting = purchase(buyer, plan, owner, telegram=True)
    begin(owner)
    assert waiting.status == "AWAITING_INPUT" and waiting.due_at is None
    body = client.get(page()).content.decode()
    assert "سفارش‌های جدید" in body and "ثبت سفارش بسته است" in body
    assert "پرداخت سفارش‌های قبلی" in body and "مهلت تحویل تا پایان تعطیلی شروع نمی‌شود" in body


def test_only_the_owner_or_staff_given_the_permission_reach_the_page(client):
    plain = staff("view_order", "change_order", "change_orderitem")
    client.force_login(plain)
    assert client.get(page()).status_code == 403
    response = client.post(page(), {"action": "start", "step": "confirm", "expected": ""})
    assert response.status_code == 403 and not Holiday.objects.exists()
    assert page() not in client.get(reverse("admin:index")).content.decode()
    plain.user_permissions.add(Permission.objects.get(codename="change_holiday"))
    client.force_login(User.objects.get(pk=plain.pk))
    assert page() in client.get(reverse("admin:index")).content.decode()
    response = client.post(page(), {"action": "start", "step": "confirm", "expected": ""})
    assert response.status_code == 302 and Holiday.objects.filter(ended_at=None).exists()


def test_the_banner_is_on_every_panel_page_while_a_holiday_is_open(client, owner):
    assert "data-holiday-banner" not in client.get(reverse("admin:index")).content.decode()
    begin(owner)
    for url in (reverse("admin:index"), queue()):
        body = client.get(url).content.decode()
        assert "data-holiday-banner" in body and 'role="status"' in body
        assert page() in body
    client.force_login(staff("view_order", "change_order", "change_orderitem"))
    body = client.get(reverse("admin:index")).content.decode()
    assert "data-holiday-banner" in body and page() not in body


# The queue


def test_a_paused_item_is_not_late_and_shows_its_stopped_time(client, owner, buyer, plan):
    make_order(buyer, plan, due_at=timezone.now() - timedelta(hours=2))
    assert {tile["label"]: tile["count"] for tile in queue_overview(owner)}["دیر شده"] == 1
    begin(owner)
    assert {tile["label"]: tile["count"] for tile in queue_overview(owner)}["دیر شده"] == 0
    body = client.get(queue()).content.decode()
    assert "در تعطیلی، " in body and 'data-clock="paused"' in body
    assert 'data-clock="overdue"' not in body
    # An order whose only work is paused asks nothing of the operator until the holiday ends.
    assert 'class="order-next-line"' not in body


def test_a_paused_item_never_hides_a_refund_owed_or_details_to_chase(client, owner, buyer, plan):
    plan.max_quantity = 3
    plan.save()
    _, (refunded, paused) = paid_order(buyer, owner, [OrderLine(plan, 2)])
    cancel(refunded, owner)
    begin(owner)
    body = client.get(queue("all")).content.decode()
    assert "را واریز کنید" in body and "تحویل بدهید" not in body
    finish(owner)
    other = User.objects.create_user("other@example.test")
    _, (asked, still) = paid_order(other, owner, [OrderLine(plan, 2)])
    act(services.request_input, asked, owner)
    begin(owner)
    body = client.get(queue("all")).content.decode()
    assert "اطلاعات فعال‌سازی را در تلگرام پیگیری کنید" in body
    still.refresh_from_db()
    assert still.sla_paused_at is not None


def test_running_deadlines_lead_the_queued_tab_and_paused_ones_follow(client, owner, buyer, plan):
    now = timezone.now()
    mixed = make_order(buyer, plan, quantity=2, due_at=now + timedelta(hours=9))
    first = mixed.items.order_by("pk").first()
    OrderItem.objects.filter(pk=first.pk).update(due_at=now + timedelta(hours=1), sla_paused_at=now)
    running = make_order(buyer, plan, due_at=now + timedelta(hours=5))
    paused_early = make_order(buyer, plan, due_at=now + timedelta(minutes=30), sla_paused_at=now)
    paused_late = make_order(buyer, plan, due_at=now + timedelta(hours=2), sla_paused_at=now)
    Holiday.objects.create(started_by=owner)
    response = client.get(queue())
    assert list(response.context["cl"].result_list) == [running, mixed, paused_early, paused_late]


def test_stopped_rows_within_one_order_follow_their_deadlines(client, owner, buyer, plan):
    now = timezone.now()
    order = make_order(buyer, plan, quantity=2, due_at=now + timedelta(hours=8), sla_paused_at=now)
    begin(owner)
    later_due, earlier_due = order.items.order_by("pk")
    OrderItem.objects.filter(pk=earlier_due.pk).update(
        due_at=now + timedelta(hours=3), sla_paused_at=now + timedelta(hours=1)
    )
    row = client.get(queue()).context["cl"].result_list[0]
    assert list(row.items.all()) == [earlier_due, later_due]


# The delivery page


def test_the_delivery_link_and_page_agree_for_a_stopped_item(client, owner, buyer, plan):
    order, item = queued(buyer, plan, owner)
    url = delivery_url(item)
    order_url = reverse("admin:orders_order_change", args=[order.pk])
    link = f'href="{url}"'
    OrderItem.objects.filter(pk=item.pk).update(sla_paused_at=timezone.now())
    order_body = client.get(order_url).content.decode()
    assert f"{link}>تحویل</a>" not in order_body
    assert f"{link}>لغو و بازپرداخت</a>" in order_body
    body = client.get(url).content.decode()
    assert "این محصول در صف انجام نیست" not in body
    assert 'name="step" value="preview"' not in body
    begin(owner)
    assert f"{link}>تحویل</a>" in client.get(order_url).content.decode()
    assert 'name="step" value="preview"' in client.get(url).content.decode()


def test_during_a_holiday_a_paused_item_is_delivered_from_its_page(client, owner, buyer, plan):
    _, item = queued(buyer, plan, owner)
    begin(owner)
    body = client.get(delivery_url(item)).content.decode()
    assert "تا پایان تعطیلی نمی‌گذرد" in body and 'name="hours"' not in body
    assert 'name="step" value="preview"' in body
    preview = client.post(delivery_url(item), form_data(item, "preview"))
    assert preview.status_code == 200 and "data-delivery-card" in preview.content.decode()
    assert client.post(delivery_url(item), form_data(item, "confirm")).status_code == 302
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.sla_paused_at is None


def test_a_page_opened_before_the_holiday_is_out_of_date_at_preview_and_confirm(
    client, owner, buyer, plan
):
    _, item = queued(buyer, plan, owner)
    before = {step: form_data(item, step) for step in ("preview", "confirm")}
    begin(owner)
    for step in ("preview", "confirm"):
        response = client.post(delivery_url(item), before[step])
        body = response.content.decode()
        assert response.status_code == 400 and STALE in body
        assert "data-delivery-card" not in body
    item.refresh_from_db()
    assert item.status == "QUEUED"


def test_a_preview_made_before_the_resume_is_out_of_date_after_it(client, owner, buyer, plan):
    _, item = queued(buyer, plan, owner)
    begin(owner)
    confirm = form_data(item, "confirm")
    finish(owner)
    response = client.post(delivery_url(item), confirm)
    assert response.status_code == 400 and STALE in response.content.decode()
    item.refresh_from_db()
    assert item.status == "QUEUED"


def test_a_delay_form_from_before_the_holiday_is_out_of_date_after_it(client, owner, buyer, plan):
    _, item = queued(buyer, plan, owner)
    expected = event_id(item)
    begin(owner)
    finish(owner)
    response = client.post(
        reverse("admin:orders_order_item_action", args=[item.order_id, item.pk, "delay"]),
        {"expected_event_id": expected, "hours": "2", "note": "تأمین‌کننده دیر کرد"},
    )
    body = response.content.decode()
    assert response.status_code == 400 and STALE in body
    assert "تأخیر این محصول قبلاً ثبت شده" not in body

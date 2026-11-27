"""A replacement request for a problem the fixed reasons do not name, in the buyer's own words.

The words may hold account details, so they are kept as customer input is: encrypted in a row
of their own that no page loads by the way, read in the panel only through a logged reveal,
absent from every message, and forgotten with the item's other details at the retention deadline.
"""

from datetime import timedelta

import pytest
import time_machine
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.utils import timezone

from apps.notifications.models import Notification
from apps.orders import services
from apps.orders.models import ClaimDetail, CredentialAccessLog, OrderItemEvent
from apps.orders.tests.test_customer_claim import delivered, url
from apps.orders.tests.test_input_clock import event_id
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_replacement import reject

pytestmark = pytest.mark.django_db(transaction=True)
OTHER = "other"
DETAIL = "پس از ورود، Netflix کد تأیید ایمیل اکانت را می‌خواهد."


def other(item, buyer, detail=DETAIL, telegram=""):
    return services.open_customer_claim(
        order_id=item.order_id,
        item_id=item.pk,
        actor=buyer,
        reason=OTHER,
        detail=detail,
        telegram=telegram,
        expected_event_id=event_id(item),
    )


def reveal_url(order, item):
    return f"/admin/orders/order/{order.pk}/items/{item.pk}/claim-detail/reveal/"


def test_another_problem_is_offered_beside_the_reasons_and_only_while_one_is(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    assert services.available_claim_reasons(item)[-1] == OTHER
    later = timezone.now() + timedelta(days=60)
    assert services.available_claim_reasons(item, now=later) == []


@pytest.mark.parametrize(
    ("detail", "refusal"),
    [
        ("", "مشکل را در چند کلمه بنویسید."),
        ("   ", "مشکل را در چند کلمه بنویسید."),
        ("ب" * 1001, "۱۰۰۰"),
    ],
)
def test_another_problem_needs_its_words(buyer, plan, operator, detail, refusal):
    _, item = delivered(buyer, plan, operator)

    with pytest.raises(ValidationError, match=refusal):
        other(item, buyer, detail)

    item.refresh_from_db()
    assert item.status == "DELIVERED"


def test_the_words_are_stored_encrypted_on_the_customers_claim(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)

    other(item, buyer, f"  {DETAIL}  ")

    event = item.events.latest("pk")
    assert (event.actor, event.claim_reason, event.note) == ("customer", OTHER, "")
    assert ClaimDetail.objects.get(event=event).text == DETAIL
    with connection.cursor() as cursor:
        cursor.execute("SELECT text FROM orders_claimdetail WHERE event_id = %s", [event.pk])
        stored = cursor.fetchone()[0]
    assert DETAIL not in stored and "$" in stored


@pytest.mark.parametrize(("detail", "kept"), [(DETAIL, True), ("  ", False)])
def test_a_named_reason_may_carry_words_too(buyer, plan, operator, detail, kept):
    _, item = delivered(buyer, plan, operator)

    services.open_customer_claim(
        order_id=item.order_id,
        item_id=item.pk,
        actor=buyer,
        reason="details_failed",
        detail=detail,
        expected_event_id=event_id(item),
    )

    assert ClaimDetail.objects.filter(text__isnull=False).exists() is kept
    assert not ClaimDetail.objects.filter(text__isnull=True).exists()


def test_the_telegram_handle_is_kept_plainly_and_shown_to_the_operator(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)

    other(item, buyer, telegram=" @Buyer_tg ")

    assert ClaimDetail.objects.get().telegram == "Buyer_tg"
    staff = Client()
    staff.force_login(operator)
    page = staff.get(f"/admin/orders/order/{order.pk}/items/{item.pk}/deliver/").content.decode()
    assert "@Buyer_tg" in page and "https://t.me/Buyer_tg" in page
    assert DETAIL not in page and not CredentialAccessLog.objects.exists()


@pytest.mark.parametrize("handle", ["abc", "with space", "@" + "a" * 33, "نام"])
def test_a_malformed_telegram_handle_is_refused(buyer, plan, operator, handle):
    _, item = delivered(buyer, plan, operator)

    with pytest.raises(ValidationError, match="آیدی تلگرام را به شکل"):
        other(item, buyer, telegram=handle)


def test_the_page_offers_the_box_and_files_the_words(client, buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    client.force_login(buyer)
    buyer.telegram_username = "buyer_tg"
    buyer.save(update_fields=["telegram_username"])
    page = client.get(f"/account/orders/{order.order_number}/").content.decode()
    assert f'name="reason" value="{OTHER}"' in page and 'name="detail"' in page
    # The linked account's handle is offered, and the buyer may change it.
    assert 'name="telegram"' in page and 'value="buyer_tg"' in page

    response = client.post(
        url(order, item),
        {"reason": OTHER, "detail": DETAIL, "expected_event_id": event_id(item)},
    )

    assert response.status_code == 302
    assert ClaimDetail.objects.get(event=item.events.latest("pk")).text == DETAIL
    page = client.get(f"/account/orders/{order.order_number}/").content.decode()
    assert DETAIL not in page


def test_no_message_carries_the_words(buyer, plan, operator, settings, mailoutbox):
    settings.OPERATOR_ALERT_EMAIL = "operator-alerts@example.test"
    _, item = delivered(buyer, plan, operator)

    other(item, buyer)

    bodies = [m.body + str(m.alternatives) + m.subject for m in mailoutbox]
    bodies += [f"{n.subject}{n.text_body}{n.html_body}" for n in Notification.objects.all()]
    assert bodies and all(DETAIL not in body for body in bodies)


def test_the_panel_reads_the_words_only_through_a_logged_reveal(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    other(item, buyer)
    staff = Client()
    staff.force_login(operator)
    replace_page = f"/admin/orders/order/{order.pk}/items/{item.pk}/deliver/"
    assert DETAIL not in staff.get(replace_page).content.decode()
    assert reveal_url(order, item) in staff.get(replace_page).content.decode()
    assert staff.get(reveal_url(order, item)).status_code == 302
    assert not CredentialAccessLog.objects.exists()

    shown = staff.post(reveal_url(order, item))

    assert shown.status_code == 200 and DETAIL in shown.content.decode()
    assert "no-store" in shown["Cache-Control"]
    log = CredentialAccessLog.objects.get()
    assert (log.target, log.source, log.user_id) == ("claim_detail", "panel", operator.pk)
    buyer_client = Client()
    buyer_client.force_login(buyer)
    assert DETAIL not in buyer_client.post(reveal_url(order, item)).content.decode()
    assert CredentialAccessLog.objects.count() == 1


def test_retention_forgets_the_words_with_the_items_details(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    other(item, buyer)
    # An open claim keeps its item's details; once answered, they follow the deadline.
    reject(item, operator)

    services.delete_expired_details(today=timezone.localdate() + timedelta(days=120))

    assert OrderItemEvent.objects.filter(order_item=item, claim_reason=OTHER).exists()
    assert not ClaimDetail.objects.exists()


def test_the_detail_migration_reverses_only_when_nothing_would_be_stranded(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    previous = ("orders", "0029_order_placement_phone")
    executor = MigrationExecutor(connection)
    newest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([previous])
        executor = MigrationExecutor(connection)
        executor.migrate(newest)
        other(item, buyer)
        with pytest.raises(RuntimeError, match="another problem"):
            MigrationExecutor(connection).migrate([previous])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert ClaimDetail.objects.count() == 1


@pytest.mark.parametrize("warranty", ["none", "days_7"])
def test_another_problem_in_the_first_48_hours_refunds_like_a_delivery_that_failed(
    buyer, plan, operator, warranty
):
    _, item = delivered(buyer, plan, operator, warranty=warranty)
    with time_machine.travel(timezone.now() + timedelta(hours=30), tick=False):
        other(item, buyer)
        item.refresh_from_db()

        assert services.refund_amount(item, "warranty_refund") == item.paid_amount


def test_another_problem_after_48_hours_is_an_ordinary_warranty_refund(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    with time_machine.travel(timezone.now() + timedelta(hours=72), tick=False):
        other(item, buyer)
        item.refresh_from_db()

        assert 0 < services.refund_amount(item, "warranty_refund") < item.paid_amount


def test_a_past_delivery_deadline_is_not_shown_beside_the_warranty_one(
    client, buyer, plan, operator
):
    order, item = delivered(buyer, plan, operator)
    client.force_login(buyer)
    with time_machine.travel(timezone.now() + timedelta(hours=72), tick=False):
        page = client.get(f"/account/orders/{order.order_number}/").content.decode()

    assert "مهلت گزارش قطع اشتراک" in page and "مهلت گزارش مشکل تحویل" not in page


def test_a_refused_request_keeps_the_reason_and_the_words_beside_its_error(
    client, buyer, plan, operator
):
    order, item = delivered(buyer, plan, operator)
    client.force_login(buyer)
    # A line break posts as two characters; the limit counts it as one, as the box does.
    words = "الف" * 332 + "\r\n" * 3

    accepted = client.post(
        url(order, item), {"reason": OTHER, "detail": words, "expected_event_id": event_id(item)}
    )
    assert accepted.status_code == 302
    _, item = delivered(buyer, plan, operator)
    refused = client.post(
        url(item.order, item),
        {
            "reason": OTHER,
            "detail": "  ",
            "telegram": "@kept_tg",
            "expected_event_id": event_id(item),
        },
    )

    body = refused.content.decode()
    box = body.partition(f'data-claim-item="{item.pk}"')[2]
    assert refused.status_code == 400 and "مشکل را در چند کلمه بنویسید." in box
    assert f'value="{OTHER}" required checked' in box and "<details open>" in body
    assert 'value="@kept_tg"' in box


def test_the_whole_claim_form_is_starred_out_of_an_error_report(client, buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    client.force_login(buyer)

    response = client.post(
        url(order, item), {"reason": OTHER, "detail": DETAIL, "expected_event_id": event_id(item)}
    )

    assert response.wsgi_request.sensitive_post_parameters == "__ALL__"


def test_a_logged_reveal_alone_also_refuses_the_reverse(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    CredentialAccessLog.objects.create(order_item=item, user=operator, target="claim_detail")
    previous = ("orders", "0029_order_placement_phone")
    try:
        with pytest.raises(RuntimeError, match="Reveals"):
            MigrationExecutor(connection).migrate([previous])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_words_on_a_named_reason_also_refuse_the_reverse(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    services.open_customer_claim(
        order_id=item.order_id,
        item_id=item.pk,
        actor=buyer,
        reason="details_failed",
        detail=DETAIL,
        telegram="buyer_tg",
        expected_event_id=event_id(item),
    )
    previous = ("orders", "0029_order_placement_phone")
    try:
        with pytest.raises(RuntimeError, match="words"):
            MigrationExecutor(connection).migrate([previous])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    assert ClaimDetail.objects.count() == 1


def test_a_handle_without_words_shows_the_handle_and_nothing_to_reveal(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    services.open_customer_claim(
        order_id=item.order_id,
        item_id=item.pk,
        actor=buyer,
        reason="details_failed",
        detail="",
        telegram="buyer_tg",
        expected_event_id=event_id(item),
    )
    assert ClaimDetail.objects.filter(text__isnull=True).count() == 1
    staff = Client()
    staff.force_login(operator)

    page = staff.get(f"/admin/orders/order/{order.pk}/items/{item.pk}/deliver/").content.decode()
    shown = staff.post(reveal_url(order, item)).content.decode()

    assert "@buyer_tg" in page and reveal_url(order, item) not in page
    assert "data-revealed-claim-detail" not in shown
    assert not CredentialAccessLog.objects.exists()


def test_a_four_letter_collectible_handle_is_accepted(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)

    other(item, buyer, telegram="@abcd")

    assert ClaimDetail.objects.get().telegram == "abcd"

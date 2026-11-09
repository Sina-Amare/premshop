import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Event
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.signals import got_request_exception
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.views.debug import SafeExceptionReporterFilter

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.core.fields import EncryptedTextField
from apps.core.formatting import format_jalali, to_persian_digits
from apps.orders import models, services
from apps.orders.tests.test_input_clock import act, event_id
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock
from apps.payments.services import record_manual_payment

pytestmark = pytest.mark.django_db(transaction=True)

VALUE = "delivered-secret-7Q2W"
NOTE = "note-secret-K9 پیش از ورود رمز را عوض نکنید."
BANK_REF = "BANKREF-4471"
LINE = services.DeliveryLine("رمز عبور", VALUE)
ALREADY_DELIVERED = (
    "این محصول قبلاً تحویل شده و اطلاعات این فرم ذخیره نشد. اطلاعات ثبت‌شده را با دکمه‌ی"
    " «نمایش اطلاعات تحویل» در صفحه‌ی سفارش ببینید."
)


def queued(buyer, plan, operator, *, telegram=False):
    plan.telegram_required, plan.input_type, plan.max_quantity = telegram, "none", 1
    plan.save()
    order = services.place_order(user=buyer, lines=[services.OrderLine(plan, 1, None)])
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref=BANK_REF
    )
    return order, order.items.get()


def deliver(item, actor, **changes):
    return services.deliver_item(
        order_id=changes.pop("order_id", item.order_id),
        item_id=item.pk,
        actor=actor,
        expected_event_id=changes.pop("expected_event_id", event_id(item)),
        lines=changes.pop("lines", [LINE]),
        note=changes.pop("note", ""),
        expires_at=changes.pop("expires_at", None),
        actual_cost=changes.pop("actual_cost", Decimal("100000")),
    )


def delivery_url(item):
    return reverse("admin:orders_order_deliver", args=[item.order_id, item.pk])


def reveal_url(item):
    return reverse("admin:orders_order_reveal_delivery", args=[item.order_id, item.pk])


def form_data(item, step, **changes):
    expires = timezone.localdate() + timedelta(days=30)
    data = {
        "step": step,
        "expected_event_id": event_id(item),
        "title_0": "ایمیل",
        "value_0": "account@example.test",
        "title_1": LINE.title,
        "value_1": VALUE,
        "note": NOTE,
        "expires_on": format_jalali(expires, with_month_name=False),
        "actual_cost": to_persian_digits(120000),
    }
    data.update(changes)
    return data


def card_of(body):
    found = re.findall(r"<section[^>]*data-delivery-card.*?</section>", body, re.DOTALL)
    assert len(found) == 1
    return found[0]


def staff_with(*codenames):
    user = User.objects.create_user("staff@example.test", is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return User.objects.get(pk=user.pk)


# The service


def test_delivery_is_ciphertext_at_rest_with_one_history_row(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    before = item.events.count()
    expires = timezone.localdate() + timedelta(days=30)
    delivery = deliver(item, operator, note=NOTE, expires_at=expires, actual_cost=Decimal("120000"))
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.delivered_at is not None
    assert item.expires_at == expires and item.actual_cost == Decimal("120000")
    event = item.events.latest("pk")
    assert item.events.count() == before + 1
    assert (event.from_status, event.to_status, event.actor, event.actor_user_id) == (
        "QUEUED",
        "DELIVERED",
        "operator",
        operator.pk,
    )
    assert (delivery.event_id, delivery.order_item_id) == (event.pk, item.pk)
    with connection.cursor() as cursor:
        cursor.execute("SELECT title, value, position FROM orders_deliveryfield")
        rows = cursor.fetchall()
        cursor.execute("SELECT note FROM orders_delivery")
        (note,) = cursor.fetchone()
    assert [(title, position) for title, _, position in rows] == [(LINE.title, 0)]
    for raw, plain in ((rows[0][1], VALUE), (note, NOTE)):
        assert re.fullmatch(r"[a-f0-9]{8}\$\S+", raw) and plain not in raw
    from apps.notifications.models import Notification

    notice = Notification.objects.get(template_name="delivery_completed")
    for part in (notice.subject, notice.text_body, notice.html_body):
        assert VALUE not in part and NOTE not in part


def test_a_failed_write_leaves_the_item_queued_with_nothing_stored(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    before = item.events.count()
    with (
        patch(
            "apps.orders.models.DeliveryField.objects.bulk_create",
            side_effect=DatabaseError("write failed"),
        ),
        pytest.raises(DatabaseError),
    ):
        deliver(item, operator)
    item.refresh_from_db()
    assert item.status == "QUEUED" and item.delivered_at is None and item.actual_cost is None
    assert item.events.count() == before
    assert not models.Delivery.objects.exists()


REFUSALS = {
    "customer": PermissionDenied,
    "unpermitted_staff": PermissionDenied,
    "inactive_operator": PermissionDenied,
    "other_order": PermissionDenied,
    "stale_page": ValidationError,
    "awaiting_input": ValidationError,
    "paused_clock": ValidationError,
    "no_lines": ValidationError,
    "title_without_value": ValidationError,
    "value_without_title": ValidationError,
    "past_expiry": ValidationError,
    "negative_cost": ValidationError,
    "fractional_cost": ValidationError,
}


@pytest.mark.parametrize("case", REFUSALS)
def test_refused_deliveries_change_nothing(buyer, plan, operator, case):
    _, item = queued(buyer, plan, operator, telegram=case == "awaiting_input")
    actor, changes = operator, {}
    if case == "customer":
        actor = buyer
    elif case == "unpermitted_staff":
        actor = User.objects.create_user("staff@example.test", is_staff=True)
    elif case == "inactive_operator":
        User.objects.filter(pk=operator.pk).update(is_active=False)
    elif case == "other_order":
        other = User.objects.create_user("other@example.test")
        changes["order_id"] = queued(other, plan, operator)[0].pk
    elif case == "stale_page":
        changes["expected_event_id"] = event_id(item) - 1
    elif case == "paused_clock":
        models.OrderItem.objects.filter(pk=item.pk).update(sla_paused_at=timezone.now())
    elif case == "no_lines":
        changes["lines"] = []
    elif case == "title_without_value":
        changes["lines"] = [services.DeliveryLine("رمز عبور", "  ")]
    elif case == "value_without_title":
        changes["lines"] = [services.DeliveryLine("", VALUE)]
    elif case == "past_expiry":
        changes["expires_at"] = timezone.localdate() - timedelta(days=1)
    elif case == "negative_cost":
        changes["actual_cost"] = Decimal("-1")
    elif case == "fractional_cost":
        changes["actual_cost"] = Decimal("1.5")
    status, before = item.status, item.events.count()
    with pytest.raises(REFUSALS[case]):
        deliver(item, actor, **changes)
    item.refresh_from_db()
    assert item.status == status and item.events.count() == before
    assert not models.Delivery.objects.exists() and not models.DeliveryField.objects.exists()


def test_a_delivered_item_refuses_every_other_built_change(buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    deliver(item, operator)
    before = item.events.count()
    for change in (
        lambda: deliver(item, operator),
        lambda: act(services.request_input, item, operator),
        lambda: act(services.receive_input, item, operator),
        lambda: act(services.resume_input, item, operator),
        lambda: services.cancel_unpaid_order(order_id=order.pk, actor=operator, note="لغو"),
        lambda: services.cancel_unpaid_order(order_id=order.pk, actor=buyer),
    ):
        with pytest.raises(ValidationError):
            change()
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.events.count() == before
    assert models.Delivery.objects.count() == 1


def test_two_confirmations_at_once_make_one_delivery(buyer, plan, operator, monkeypatch):
    _, item = queued(buyer, plan, operator)
    expected, before = event_id(item), item.events.count()
    original = services._input_event
    entered, release, pids = Event(), Event(), Queue()

    def held(*args, **kwargs):
        event = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return event

    monkeypatch.setattr(services, "_input_event", held)

    def submit():
        return deliver(item, operator, expected_event_id=expected)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(in_connection, pids, submit)
        holder = pids.get(timeout=10)
        assert entered.wait(10)
        contender = pool.submit(in_connection, pids, submit)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        assert leader.result(timeout=10).order_item_id == item.pk
        assert contender.result(timeout=10) is None  # in_connection returns None on refusal
    assert models.Delivery.objects.count() == 1 and models.DeliveryField.objects.count() == 1
    assert item.events.count() == before + 1


def test_a_corrected_set_from_a_second_tab_is_refused_and_not_stored(client, buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    client.force_login(operator)
    first_tab = form_data(item, "confirm")
    second_tab = form_data(item, "confirm", value_1="corrected-secret-B8")
    assert client.post(delivery_url(item), first_tab).status_code == 302
    original = EncryptedTextField.from_db_value

    def guarded(self, value, expression, connection):
        assert value in (None, ""), "the repeated confirmation decrypted a stored value"
        return original(self, value, expression, connection)

    with patch.object(EncryptedTextField, "from_db_value", guarded):
        response = client.post(delivery_url(item), second_tab)
    body = response.content.decode()
    assert response.status_code == 400 and 'role="alert"' in body
    assert ALREADY_DELIVERED in body
    assert "corrected-secret-B8" not in body and VALUE not in body
    assert models.Delivery.objects.count() == 1
    assert item.events.filter(to_status="DELIVERED").count() == 1
    content = services.reveal_delivery(
        order_id=item.order_id, item_id=item.pk, actor=operator, ip=None
    )
    assert [line.value for line in content.lines] == ["account@example.test", VALUE]


def test_another_operators_late_confirmation_is_still_refused(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    expected = event_id(item)
    deliver(item, operator, expected_event_id=expected)
    second = User.objects.create_superuser("second-operator@example.test")
    with pytest.raises(ValidationError):
        deliver(item, second, expected_event_id=expected)
    assert models.Delivery.objects.count() == 1


def test_a_page_from_before_an_input_round_trip_gets_the_stale_refusal(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    old_page = event_id(item)
    act(services.request_input, item, operator)
    act(services.resume_input, item, operator)
    deliver(item, operator)
    with pytest.raises(ValidationError) as refused:
        deliver(item, operator, expected_event_id=old_page)
    assert "صفحه را دوباره باز کنید" in " ".join(refused.value.messages)
    assert models.Delivery.objects.count() == 1


def test_reveal_commits_its_log_row_before_returning(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    expires = timezone.localdate() + timedelta(days=30)
    deliver(item, operator, note=NOTE, expires_at=expires)
    content = services.reveal_delivery(
        order_id=item.order_id, item_id=item.pk, actor=operator, ip="127.0.0.1"
    )
    assert content == services.DeliveryContent((LINE,), NOTE, expires)
    assert VALUE not in repr(content) and VALUE not in repr(LINE)
    other_connection = connection.copy()
    try:
        with other_connection.cursor() as cursor:
            cursor.execute(
                "SELECT order_item_id, user_id, ip, target FROM orders_credentialaccesslog"
            )
            assert cursor.fetchall() == [(item.pk, operator.pk, "127.0.0.1", "delivery")]
    finally:
        other_connection.close()
    with transaction.atomic(), pytest.raises(RuntimeError, match="durable"):
        services.reveal_delivery(order_id=item.order_id, item_id=item.pk, actor=operator, ip=None)
    assert models.CredentialAccessLog.objects.count() == 1


def test_reveal_writes_its_access_row_before_any_value_is_decrypted(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    deliver(item, operator, note=NOTE)
    original, logged_first = EncryptedTextField.from_db_value, []

    def watched(self, value, expression, connection):
        if value:
            logged_first.append(
                models.CredentialAccessLog.objects.filter(target="delivery").exists()
            )
        return original(self, value, expression, connection)

    with patch.object(EncryptedTextField, "from_db_value", watched):
        content = services.reveal_delivery(
            order_id=item.order_id, item_id=item.pk, actor=operator, ip=None
        )
    assert content.lines == (LINE,) and logged_first == [True, True]


def test_a_reveal_that_cannot_decrypt_leaves_no_access_row(buyer, plan, operator, settings):
    _, item = queued(buyer, plan, operator)
    deliver(item, operator, note=NOTE)
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with pytest.raises(ImproperlyConfigured):
        services.reveal_delivery(order_id=item.order_id, item_id=item.pk, actor=operator, ip=None)
    assert not models.CredentialAccessLog.objects.exists()


@pytest.mark.parametrize("kind", ["customer", "unpermitted_staff", "other_order", "undelivered"])
def test_reveal_refuses_or_logs_nothing_without_a_delivery(buyer, plan, operator, kind):
    _, item = queued(buyer, plan, operator)
    if kind != "undelivered":
        deliver(item, operator)
    actor = {"customer": buyer, "unpermitted_staff": staff_with("view_order")}.get(kind, operator)
    order_id = item.order_id + 1000 if kind == "other_order" else item.order_id
    if kind == "undelivered":
        assert (
            services.reveal_delivery(order_id=order_id, item_id=item.pk, actor=actor, ip=None)
            is None
        )
    else:
        with pytest.raises((PermissionDenied, models.OrderItem.DoesNotExist)):
            services.reveal_delivery(order_id=order_id, item_id=item.pk, actor=actor, ip=None)
    assert not models.CredentialAccessLog.objects.exists()


def test_customer_input_reveals_are_labelled_as_such(buyer, plan, operator):
    plan.input_type, plan.max_quantity = Plan.InputType.EMAIL, 1
    plan.save()
    order = services.place_order(
        user=buyer, lines=[services.OrderLine(plan, 1, "target@example.test")]
    )
    services.reveal_customer_input(
        order_id=order.pk, item_id=order.items.get().pk, actor=operator, ip=None
    )
    assert models.CredentialAccessLog.objects.get().target == "customer_input"


def test_model_text_never_holds_a_value(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    delivery = deliver(item, operator, note=NOTE)
    for obj in (delivery, *delivery.fields.all(), item, delivery.event):
        for text in (str(obj), repr(obj)):
            assert VALUE not in text and "note-secret" not in text


def test_existing_access_rows_become_customer_input_reveals_and_reverse(buyer, plan, operator):
    previous = ("orders", "0017_order_support_identifier")
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        old = executor.loader.project_state([previous]).apps
        order = old.get_model("orders", "Order").objects.create(
            user_id=buyer.pk, subtotal=200000, total_amount=200000
        )
        item = old.get_model("orders", "OrderItem").objects.create(
            order=order,
            plan_id=plan.pk,
            price_snapshot=200000,
            cost_snapshot=100000,
            product_snapshot={},
        )
        Log = old.get_model("orders", "CredentialAccessLog")
        row = Log.objects.create(order_item_id=item.pk, user_id=operator.pk)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        assert models.CredentialAccessLog.objects.get(pk=row.pk).target == "customer_input"
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        assert Log.objects.filter(pk=row.pk).exists()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


# The panel


def test_delivery_page_shows_the_three_columns_and_changes_nothing(client, buyer, plan, operator):
    Product.objects.filter(pk=plan.product_id).update(
        delivery_template="ایمیل و <b>رمز</b>\nبا کد دومرحله‌ای"
    )
    _, item = queued(buyer, plan, operator)
    Plan.objects.filter(pk=plan.pk).update(supplier_url="https://supplier.example/listing")
    before = item.events.count()
    client.force_login(operator)
    response = client.get(delivery_url(item), HTTP_ACCEPT_ENCODING="gzip")
    body = response.content.decode()
    assert response.status_code == 200
    assert buyer.email in body and BANK_REF in body
    assert "https://supplier.example/listing" in body
    assert "ایمیل و &lt;b&gt;رمز&lt;/b&gt;\nبا کد دومرحله‌ای" in body
    request = reverse("admin:orders_order_input_action", args=[item.order_id, item.pk, "request"])
    assert request in body
    for name in ("value_0", "value_5"):
        (tag,) = re.findall(rf'<input[^>]*name="{name}"[^>]*>', body)
        assert 'autocomplete="off"' in tag
    assert response.headers["Content-Encoding"] == "identity"
    assert "no-store" in response.headers["Cache-Control"]
    item.refresh_from_db()
    assert item.status == "QUEUED" and item.events.count() == before


def test_bank_reference_needs_payment_view_permission(client, buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    client.force_login(staff_with("view_order", "change_order", "change_orderitem"))
    response = client.get(delivery_url(item))
    assert response.status_code == 200 and BANK_REF not in response.content.decode()


def test_preview_carries_values_only_inside_the_form(client, buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    client.force_login(operator)
    response = client.post(
        delivery_url(item), form_data(item, "preview"), HTTP_ACCEPT_ENCODING="gzip"
    )
    body = response.content.decode()
    assert response.status_code == 200
    card = card_of(body)
    assert VALUE in card and "note-secret-K9" in card
    assert re.search(rf'<input type="hidden" name="value_1" value="{VALUE}"', body)
    assert body.count(VALUE) == 2
    assert response.headers["Content-Encoding"] == "identity"
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert not models.Delivery.objects.exists()
    assert list(get_messages(response.wsgi_request)) == []
    assert VALUE not in repr(dict(client.session.items()))
    assert not any(VALUE.encode() in raw for raw in cache._cache.values())
    edit = client.post(delivery_url(item), form_data(item, "edit"))
    assert re.search(rf'name="value_1" value="{VALUE}"', edit.content.decode())
    assert edit.headers["Content-Encoding"] == "identity"


@pytest.mark.parametrize("state", ["awaiting_input", "paused_clock"])
def test_preview_refuses_an_item_that_cannot_be_delivered(client, buyer, plan, operator, state):
    _, item = queued(buyer, plan, operator)
    client.force_login(operator)
    data = form_data(item, "preview")
    if state == "awaiting_input":
        act(services.request_input, item, operator)
    else:
        models.OrderItem.objects.filter(pk=item.pk).update(sla_paused_at=timezone.now())
    response = client.post(delivery_url(item), data)
    body = response.content.decode()
    assert response.status_code == 400 and 'role="alert"' in body
    assert "data-delivery-card" not in body and VALUE not in body
    assert response.headers["Content-Encoding"] == "identity"


def test_preview_equals_the_delivered_content(client, buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    client.force_login(operator)
    preview = card_of(client.post(delivery_url(item), form_data(item, "preview")).content.decode())
    response = client.post(delivery_url(item), form_data(item, "confirm"))
    assert response.status_code == 302
    assert response.url == reverse("admin:orders_order_change", args=[item.order_id])
    revealed = client.post(reveal_url(item), HTTP_ACCEPT_ENCODING="gzip")
    assert card_of(revealed.content.decode()) == preview
    assert revealed.headers["Content-Encoding"] == "identity"
    assert "no-store" in revealed.headers["Cache-Control"]
    item.refresh_from_db()
    assert item.status == "DELIVERED" and item.actual_cost == Decimal("120000")
    assert models.CredentialAccessLog.objects.get().target == "delivery"
    assert client.get(reveal_url(item)).status_code == 302
    assert models.CredentialAccessLog.objects.count() == 1


def test_no_panel_page_shows_or_decrypts_a_delivered_value(client, buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    client.force_login(operator)
    client.post(delivery_url(item), form_data(item, "confirm"))
    original = EncryptedTextField.from_db_value

    def guarded(self, value, expression, connection):
        assert value in (None, ""), "a panel page decrypted a stored value"
        return original(self, value, expression, connection)

    pages = [
        reverse("admin:orders_order_change", args=[order.pk]),
        reverse("admin:orders_order_changelist") + "?tab=all",
        reverse("admin:orders_order_changelist") + "?q=" + buyer.email,
        delivery_url(item),
    ]
    with patch.object(EncryptedTextField, "from_db_value", guarded):
        for page in pages:
            response = client.get(page)
            body = response.content.decode()
            assert response.status_code == 200, page
            assert VALUE not in body and "note-secret" not in body and "account@" not in body
    detail = client.get(pages[0]).content.decode()
    assert reveal_url(item) in detail and f'href="{delivery_url(item)}">تحویل</a>' not in detail


def test_order_page_offers_delivery_for_a_queued_item(client, buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    client.force_login(operator)
    body = client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
    assert delivery_url(item) in body and reveal_url(item) not in body


@pytest.mark.parametrize("step", ["preview", "confirm"])
def test_a_crash_reports_no_typed_value(client, buyer, plan, operator, settings, caplog, step):
    _, item = queued(buyer, plan, operator)
    settings.DEBUG = False
    client.raise_request_exception = False
    client.force_login(operator)
    reported = []

    def capture(sender, request, **kwargs):
        reported.append(SafeExceptionReporterFilter().get_post_parameters(request))

    target = "apps.orders.admin.deliver_item" if step == "confirm" else "apps.orders.admin.render"
    got_request_exception.connect(capture)
    try:
        with patch(target, side_effect=RuntimeError("delivery failed")):
            response = client.post(delivery_url(item), form_data(item, step))
    finally:
        got_request_exception.disconnect(capture)
    assert response.status_code == 500
    assert VALUE not in response.content.decode() and VALUE not in caplog.text
    (posted,) = reported
    assert VALUE not in repr(dict(posted)) and "note-secret" not in repr(dict(posted))
    assert not models.Delivery.objects.exists()


@pytest.mark.parametrize(
    "kind", ["customer", "unpermitted_staff", "view_only_staff", "mismatched_item"]
)
def test_unpermitted_accounts_and_mismatched_items_are_refused(client, buyer, plan, operator, kind):
    _, item = queued(buyer, plan, operator)
    actor = {
        "customer": lambda: buyer,
        "unpermitted_staff": staff_with,
        "view_only_staff": lambda: staff_with("view_order", "view_orderitem"),
    }.get(kind, lambda: operator)()
    client.force_login(actor)
    url = delivery_url(item)
    if kind == "mismatched_item":
        url = reverse("admin:orders_order_deliver", args=[item.order_id, item.pk + 1])
    for response in (client.get(url), client.post(url, form_data(item, "confirm"))):
        assert response.status_code in (302, 403, 404)
        assert VALUE not in response.content.decode()
    assert not models.Delivery.objects.exists()


def test_confirm_needs_csrf_and_only_get_and_post_are_allowed(buyer, plan, operator):
    _, item = queued(buyer, plan, operator)
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    client.get(delivery_url(item))
    assert client.post(delivery_url(item), form_data(item, "confirm")).status_code == 403
    assert not models.Delivery.objects.exists()
    client = Client()
    client.force_login(operator)
    assert client.put(delivery_url(item)).status_code == 405
    assert client.put(reveal_url(item)).status_code == 405


@pytest.mark.parametrize(
    "field, value",
    [
        ("value_1", ""),
        ("expires_on", "۱۴۰۵/۱۳/۰۱"),
        ("expires_on", "دیروز"),
        ("actual_cost", "-5"),
        ("expected_event_id", "0"),
    ],
)
def test_invalid_or_stale_forms_explain_and_store_nothing(
    client, buyer, plan, operator, field, value
):
    _, item = queued(buyer, plan, operator)
    client.force_login(operator)
    response = client.post(delivery_url(item), form_data(item, "confirm", **{field: value}))
    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert response.headers["Content-Encoding"] == "identity"
    assert not models.Delivery.objects.exists()

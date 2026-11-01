from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Plan
from apps.core.formatting import to_persian_digits
from apps.orders import models, services

pytestmark = pytest.mark.django_db(transaction=True)
VALUE = "private-target@example.test"


@pytest.fixture
def input_order(buyer, plan):
    plan.input_type = Plan.InputType.EMAIL
    plan.max_quantity = 1
    plan.save()
    return services.place_order(user=buyer, lines=[services.OrderLine(plan, 1, VALUE)])


@pytest.fixture
def operator():
    return User.objects.create_superuser("operator@example.test")


def reveal_url(order, item_id=None):
    return reverse(
        "admin:orders_order_reveal_customer_input",
        args=[order.pk, item_id or order.items.only("pk").get().pk],
    )


def reveal(order, operator, **kwargs):
    return services.reveal_customer_input(
        order_id=order.pk,
        item_id=order.items.only("pk").get().pk,
        actor=operator,
        ip=kwargs.get("ip", "127.0.0.1"),
    )


def test_order_buttons_follow_frozen_input_requirement(client, input_order, operator):
    item = input_order.items.defer("customer_input").get()
    plan = item.plan
    plan.input_type = Plan.InputType.NONE
    plan.save()
    no_input = Plan.objects.create(
        product=plan.product,
        title="بدون اطلاعات",
        sale_price=100,
        cost_price=50,
        input_type=Plan.InputType.NONE,
        telegram_required=False,
    )
    other = User.objects.create_user("another@example.test")
    plain = services.place_order(user=other, lines=[services.OrderLine(no_input, 1)])
    no_input.input_type = Plan.InputType.EMAIL
    no_input.max_quantity = 1
    no_input.save()
    client.force_login(operator)
    detail = reverse("admin:orders_order_change", args=[input_order.pk])
    body = client.get(detail).content.decode()
    assert "data-customer-input-reveal" in body
    assert VALUE not in body
    assert (
        "data-customer-input-reveal"
        not in client.get(reverse("admin:orders_order_change", args=[plain.pk])).content.decode()
    )


def test_service_commits_before_returning_and_never_copies_the_value(input_order, operator):
    assert reveal(input_order, operator) == VALUE
    other_connection = connection.copy()
    try:
        with other_connection.cursor() as cursor:
            cursor.execute("SELECT order_item_id, user_id, ip FROM orders_credentialaccesslog")
            assert cursor.fetchall() == [
                (input_order.items.only("pk").get().pk, operator.pk, "127.0.0.1")
            ]
    finally:
        other_connection.close()
    assert {field.name for field in models.CredentialAccessLog._meta.fields} == {
        "id",
        "order_item",
        "user",
        "ip",
        "created_at",
    }


def test_service_refuses_nested_transactions(input_order, operator):
    with transaction.atomic(), pytest.raises(RuntimeError, match="durable"):
        reveal(input_order, operator)
    assert not models.CredentialAccessLog.objects.exists()


@pytest.mark.parametrize("order_permission", ["view_order", "change_order"])
@pytest.mark.parametrize("item_permission", ["view_orderitem", "change_orderitem"])
def test_both_models_accept_view_or_change(client, input_order, order_permission, item_permission):
    staff = User.objects.create_user("reader@example.test", is_staff=True)
    staff.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="orders", codename__in=[order_permission, item_permission]
        )
    )
    client.force_login(staff)
    response = client.post(reveal_url(input_order))
    assert response.status_code == 200
    assert VALUE in response.content.decode()
    assert models.CredentialAccessLog.objects.get().user_id == staff.pk


@pytest.mark.parametrize(
    "permission", [None, "view_order", "change_order", "view_orderitem", "change_orderitem"]
)
def test_one_models_permission_is_insufficient(client, input_order, permission):
    staff = User.objects.create_user("reader@example.test", is_staff=True)
    if permission:
        staff.user_permissions.add(
            Permission.objects.get(content_type__app_label="orders", codename=permission)
        )
    client.force_login(staff)
    response = client.post(reveal_url(input_order))
    assert response.status_code == 403
    assert VALUE not in response.content.decode()
    assert client.get(reveal_url(input_order)).status_code == 403
    with pytest.raises(PermissionDenied):
        reveal(input_order, staff)
    assert not models.CredentialAccessLog.objects.exists()


def test_unpermitted_staff_cannot_tell_which_orders_exist(client, input_order):
    client.force_login(User.objects.create_user("reader@example.test", is_staff=True))
    missing = reverse("admin:orders_order_reveal_customer_input", args=[input_order.pk + 1000, 1])
    assert client.post(missing).status_code == 403
    assert client.post(reveal_url(input_order)).status_code == 403


def test_a_non_staff_account_holding_the_permissions_cannot_reveal(client, input_order):
    holder = User.objects.create_user("holder@example.test")
    holder.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="orders", codename__in=["view_order", "view_orderitem"]
        )
    )
    client.force_login(holder)
    assert client.post(reveal_url(input_order)).status_code == 302
    with pytest.raises(PermissionDenied):
        reveal(input_order, holder)
    assert not models.CredentialAccessLog.objects.exists()


@pytest.mark.parametrize("identity", ["anonymous", "customer", "inactive_staff"])
def test_untrusted_accounts_never_reveal(client, buyer, input_order, operator, identity):
    if identity == "customer":
        client.force_login(buyer)
    elif identity == "inactive_staff":
        operator.is_active = False
        operator.save(update_fields=["is_active"])
        client.force_login(operator)
    response = client.post(reveal_url(input_order))
    assert response.status_code == 302
    assert VALUE not in response.content.decode()
    assert not models.CredentialAccessLog.objects.exists()
    if identity != "anonymous":
        with pytest.raises(PermissionDenied):
            reveal(input_order, buyer if identity == "customer" else operator)


def test_exact_pair_of_ids_is_required(client, buyer, input_order, operator, plan):
    other = User.objects.create_user("another@example.test")
    foreign = services.place_order(user=other, lines=[services.OrderLine(plan, 1, VALUE)])
    foreign_id = foreign.items.only("pk").get().pk
    client.force_login(operator)
    assert client.post(reveal_url(input_order, foreign_id)).status_code == 404
    with pytest.raises(models.OrderItem.DoesNotExist):
        services.reveal_customer_input(
            order_id=input_order.pk, item_id=foreign_id, actor=operator, ip=None
        )
    assert not models.CredentialAccessLog.objects.exists()


def test_reveals_are_individual_and_history_needs_no_key(client, input_order, operator, settings):
    client.force_login(operator)
    url = reveal_url(input_order)
    for _ in range(2):
        assert VALUE in client.post(url).content.decode()
    assert models.CredentialAccessLog.objects.count() == 2
    settings.FIELD_ENCRYPTION_KEY = ""
    detail = reverse("admin:orders_order_change", args=[input_order.pk])
    response = client.get(detail)
    assert response.status_code == 200
    assert VALUE not in response.content.decode()
    rows = list(response.context["input_access_log"])
    assert len(rows) == 2
    assert all(row.user_id == operator.pk for row in rows)
    assert "order_item" not in rows[0]._state.fields_cache
    assert operator.email in response.content.decode()
    assert 'id="input-access-history"' in response.content.decode()
    response = client.get(url)
    assert response.status_code == 302 and response.url == detail
    assert models.CredentialAccessLog.objects.count() == 2


@pytest.mark.parametrize("method", ["head", "put", "patch", "delete", "options"])
def test_other_methods_cannot_reveal(client, input_order, operator, method):
    client.force_login(operator)
    response = getattr(client, method)(reveal_url(input_order))
    assert response.status_code == 405
    assert response.headers["Allow"] == "GET, POST"
    assert not models.CredentialAccessLog.objects.exists()


def test_csrf_and_expired_session_return_to_masked_order(input_order, operator, settings):
    from django.contrib.sessions.models import Session
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    operator.set_password("test-only-password-24")
    operator.save(update_fields=["password"])
    client.force_login(operator)
    url = reveal_url(input_order)
    detail = reverse("admin:orders_order_change", args=[input_order.pk])
    client.get(detail)
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    assert client.post(url).status_code == 403
    Session.objects.filter(session_key=client.session.session_key).update(
        expire_date=timezone.now() - timedelta(seconds=1)
    )
    response = client.post(url, {"csrfmiddlewaretoken": token})
    assert response.status_code == 302 and reverse("admin:login") in response.url
    login_url = response.url
    assert client.get(login_url).status_code == 200
    response = client.post(
        login_url,
        {
            "username": operator.email,
            "password": "test-only-password-24",
            "csrfmiddlewaretoken": client.cookies[settings.CSRF_COOKIE_NAME].value,
        },
    )
    assert response.status_code == 302 and response.url == url
    response = client.get(response.url, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [(detail, 302)]
    assert VALUE not in response.content.decode()
    assert not models.CredentialAccessLog.objects.exists()


def test_blank_input_is_neutral_and_not_logged(client, input_order, operator):
    item = input_order.items.get()
    item.customer_input = ""
    item.save(update_fields=["customer_input"])
    client.force_login(operator)
    response = client.post(reveal_url(input_order))
    assert response.status_code == 200
    assert "اطلاعاتی برای نمایش وجود ندارد" in response.content.decode()
    assert not models.CredentialAccessLog.objects.exists()


@pytest.mark.parametrize("failure", ["decrypt", "damaged", "insert", "commit"])
def test_failures_reach_existing_500_without_value_or_row(
    client, input_order, operator, settings, failure, caplog
):
    settings.DEBUG = False
    client.raise_request_exception = False
    client.force_login(operator)
    url = reveal_url(input_order)
    if failure == "decrypt":
        settings.FIELD_ENCRYPTION_KEY = ""
        response = client.post(url)
    elif failure == "damaged":
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE orders_orderitem SET customer_input = 'broken' WHERE order_id = %s",
                [input_order.pk],
            )
        response = client.post(url)
    else:
        target = (
            "apps.orders.models.CredentialAccessLog.objects.create"
            if failure == "insert"
            else "django.db.backends.base.base.BaseDatabaseWrapper.commit"
        )
        with patch(target, side_effect=DatabaseError("Access record could not be saved")):
            response = client.post(url)
    assert response.status_code == 500
    assert "مشکلی در سرور پیش آمد" in response.content.decode()
    assert VALUE not in response.content.decode() and VALUE not in caplog.text
    assert not models.CredentialAccessLog.objects.exists()


def test_reveal_has_privacy_headers_and_is_not_gzipped(client, input_order, operator):
    client.force_login(operator)
    response = client.post(reveal_url(input_order), HTTP_ACCEPT_ENCODING="gzip")
    assert response.status_code == 200 and VALUE in response.content.decode()
    assert response.headers["Content-Encoding"] == "identity"
    assert "private" in response.headers["Cache-Control"]
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize(
    "ip, expected", [("2001:db8::1", "2001:db8::1"), ("unknown", None), (None, None)]
)
def test_log_accepts_only_valid_ip_addresses(input_order, operator, ip, expected):
    reveal(input_order, operator, ip=ip)
    assert models.CredentialAccessLog.objects.get().ip == expected


def test_reveal_does_not_trust_forwarded_ip_without_proxy_configuration(
    client, input_order, operator, settings
):
    settings.TRUST_PROXY_HEADERS = False
    client.force_login(operator)
    client.post(
        reveal_url(input_order),
        REMOTE_ADDR="203.0.113.7",
        HTTP_X_FORWARDED_FOR="198.51.100.9",
    )
    assert models.CredentialAccessLog.objects.get().ip == "203.0.113.7"


def test_access_row_protects_its_item_and_staff_user(input_order, operator):
    from django.db.models.deletion import ProtectedError

    reveal(input_order, operator)
    with pytest.raises(ProtectedError):
        operator.delete()
    with pytest.raises(ProtectedError):
        input_order.items.only("pk").get().delete()


def test_log_retention_only_deletes_old_access_rows(input_order, operator):
    from apps.orders.tasks import clean_credential_access_logs

    for _ in range(2):
        reveal(input_order, operator)
    now = timezone.now()
    old, recent = models.CredentialAccessLog.objects.order_by("pk")
    models.CredentialAccessLog.objects.filter(pk=old.pk).update(
        created_at=now - timedelta(days=365, seconds=1)
    )
    models.CredentialAccessLog.objects.filter(pk=recent.pk).update(
        created_at=now - timedelta(days=365)
    )
    with patch("apps.orders.tasks.timezone.now", return_value=now):
        assert clean_credential_access_logs.call_local() == 1
        assert clean_credential_access_logs.call_local() == 0
    assert models.CredentialAccessLog.objects.get().pk == recent.pk
    assert input_order.items.get().customer_input == VALUE
    assert input_order.items.get().events.count() == 1


def test_reveal_only_returns_selected_item_and_escapes_legacy_input(client, buyer, plan, operator):
    plan.input_type = Plan.InputType.EMAIL
    plan.max_quantity = 1
    plan.save()
    second = Plan.objects.create(
        product=plan.product,
        title="حساب دوم",
        sale_price=100,
        cost_price=50,
        input_type=Plan.InputType.EMAIL,
        telegram_required=False,
    )
    order = services.place_order(
        user=buyer,
        lines=[
            services.OrderLine(plan, 1, VALUE),
            services.OrderLine(second, 1, "other-private@example.test"),
        ],
    )
    item = order.items.first()
    item.customer_input = '<script>alert("private")</script>'
    item.save(update_fields=["customer_input"])
    client.force_login(operator)
    response = client.post(reveal_url(order, item.pk))
    body = response.content.decode()
    assert response.status_code == 200
    assert "&lt;script&gt;" in body and '<script>alert("private")</script>' not in body
    assert "other-private@example.test" not in body
    assert models.CredentialAccessLog.objects.get().order_item_id == item.pk


def test_access_history_contains_only_this_orders_rows(client, input_order, operator, plan):
    other = User.objects.create_user("another@example.test")
    foreign = services.place_order(user=other, lines=[services.OrderLine(plan, 1, VALUE)])
    reveal(foreign, operator)
    reveal(input_order, operator)
    client.force_login(operator)
    response = client.get(reverse("admin:orders_order_change", args=[input_order.pk]))
    assert [row.order_item_id for row in response.context["input_access_log"]] == [
        input_order.items.only("pk").get().pk
    ]


def test_access_history_shows_distinct_local_times_on_the_same_day(client, input_order, operator):
    import time_machine

    with time_machine.travel("2026-11-01 09:01:00+00:00", tick=False):
        reveal(input_order, operator)
    with time_machine.travel("2026-11-01 10:12:00+00:00", tick=False):
        reveal(input_order, operator)
    client.force_login(operator)
    body = client.get(reverse("admin:orders_order_change", args=[input_order.pk])).content.decode()
    assert "۱۲:۳۱" in body and "۱۳:۴۲" in body


def test_reveal_failure_is_reported_without_decrypted_material(
    client, input_order, operator, settings
):
    import json

    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.transport import Transport

    from apps.core.observability import sentry_options

    captured = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    captured.append(item.payload.json)

    settings.DEBUG = False
    client.raise_request_exception = False
    client.force_login(operator)
    reporter = sentry_sdk.Client(
        **sentry_options(dsn="https://public@example.test/1", environment="test"),
        transport=Capture(),
        default_integrations=False,
        integrations=[DjangoIntegration()],
    )
    with sentry_sdk.isolation_scope() as scope:
        scope.set_client(reporter)
        with patch.object(
            models.CredentialAccessLog.objects,
            "create",
            side_effect=DatabaseError("Cannot save access record"),
        ):
            response = client.post(reveal_url(input_order))
    reporter.close()
    assert response.status_code == 500 and captured
    assert VALUE not in json.dumps(captured)
    assert "reveal_customer_input" in json.dumps(captured)
    assert not models.CredentialAccessLog.objects.exists()


def test_retention_is_queued_and_does_not_delete_recent_accesses(input_order, operator):
    from huey.contrib.djhuey import HUEY

    from apps.orders.tasks import clean_credential_access_logs

    reveal(input_order, operator)
    models.CredentialAccessLog.objects.update(created_at=timezone.now() - timedelta(days=366))
    clean_credential_access_logs()
    assert models.CredentialAccessLog.objects.count() == 1
    HUEY.execute(HUEY.dequeue())
    assert not models.CredentialAccessLog.objects.exists()


@pytest.mark.browser
@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("javascript", [True, False])
def test_input_reveal_and_read_only_history_in_browser(
    browser, live_server, client, input_order, operator, settings, tmp_path, width, javascript
):
    from playwright.sync_api import expect

    settings.DEBUG = False
    item = input_order.items.get()
    long_value = "a" * 63 + "@" + "b" * 63 + "." + "c" * 63 + ".example.test"
    item.customer_input = long_value
    item.save(update_fields=["customer_input"])
    client.force_login(operator)
    context = browser.new_context(
        viewport={"width": width, "height": 1000},
        java_script_enabled=javascript,
        extra_http_headers={"Accept-Encoding": "gzip"},
    )
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    detail = reverse("admin:orders_order_change", args=[input_order.pk])
    url = reveal_url(input_order)
    try:
        page.goto(live_server.url + detail)
        assert page.url.endswith(detail)
        expect(page.locator("#input-access-history")).to_contain_text(
            "هنوز اطلاعاتی نمایش داده نشده."
        )
        assert long_value not in page.content()
        page.screenshot(path=str(tmp_path / f"masked-{width}-{javascript}.png"), full_page=True)
        button = page.locator("[data-customer-input-reveal]")
        assert button.bounding_box()["height"] >= 44
        button.focus()
        with page.expect_response(lambda response: response.request.method == "POST") as sent:
            button.press("Enter")
        assert sent.value.request.method == "POST"
        assert sent.value.url.endswith(url)
        assert sent.value.headers["content-encoding"] == "identity"
        expect(page.locator("[data-revealed-input]")).to_have_text(long_value)
        assert page.url.endswith(url)
        expect(page.locator("#input-reveal-title")).to_contain_text("اطلاعات مشتری")
        assert (
            page.get_by_role("link", name="بازگشت به سفارش", exact=True).bounding_box()["height"]
            >= 44
        )
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"revealed-{width}-{javascript}.png"), full_page=True)
        page.get_by_role("link", name="بازگشت به سفارش", exact=True).click()
        expect(page.locator("#input-access-history")).to_contain_text(operator.email)
        history = page.locator("#input-access-history")
        revealed_at = models.CredentialAccessLog.objects.get().created_at
        expect(history.locator("time")).to_contain_text(
            to_persian_digits(timezone.localtime(revealed_at).strftime("%H:%M"))
        )
        assert history.locator("li").count() == 1
        assert not history.locator("input, button, textarea, select").count()
        assert long_value not in page.content()
        original_key = settings.FIELD_ENCRYPTION_KEY
        settings.FIELD_ENCRYPTION_KEY = ""
        page.reload()
        expect(history).to_contain_text(operator.email)
        assert page.url.endswith(detail)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"history-{width}-{javascript}.png"), full_page=True)
        page.goto(live_server.url + url)
        assert page.url.endswith(detail)
        expect(history).to_contain_text(operator.email)
        assert models.CredentialAccessLog.objects.count() == 1
        item.customer_input = ""
        item.save(update_fields=["customer_input"])
        page.locator("[data-customer-input-reveal]").click()
        expect(page.locator("body")).to_contain_text("اطلاعاتی برای نمایش وجود ندارد.")
        assert page.url.endswith(url)
        page.screenshot(path=str(tmp_path / f"blank-{width}-{javascript}.png"), full_page=True)
        page.get_by_role("link", name="بازگشت به سفارش", exact=True).click()
        settings.FIELD_ENCRYPTION_KEY = original_key
        item.customer_input = long_value
        item.save(update_fields=["customer_input"])
        settings.FIELD_ENCRYPTION_KEY = ""
        page.locator("[data-customer-input-reveal]").click()
        expect(page.get_by_role("heading", name="مشکلی در سرور پیش آمد")).to_be_visible()
        assert page.url.endswith(url)
        assert long_value not in page.content()
        page.screenshot(path=str(tmp_path / f"error-{width}-{javascript}.png"), full_page=True)
        assert models.CredentialAccessLog.objects.count() == 1
    finally:
        context.close()

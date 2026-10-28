import re

import pytest
import time_machine
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts import otp, services
from apps.accounts.models import User

pytestmark = pytest.mark.django_db
PASSWORD = "a-long-new-passphrase-730!"  # noqa: S105 — fixture, not a credential


@pytest.fixture
def customer(client):
    user = User.objects.create_user("recovery@example.test", "previous-passphrase-420!")
    client.force_login(user)
    return user


def request_code(client):
    response = client.post(reverse("account-password-recovery"), {"action": "request"})
    assert response.status_code == 200
    text = mail.outbox[-1].body.translate(otp.PERSIAN_TO_ASCII_DIGITS)
    return re.search(r"\b[0-9]{6}\b", text).group()


def submit(client, code, password=PASSWORD):
    return client.post(
        reverse("account-password-recovery"),
        {"action": "change", "code": code, "new_password1": password, "new_password2": password},
    )


def test_recovery_codes_are_purpose_and_session_bound():
    issued = otp.issue("a@example.test", purpose="password_reset", binding="session-a")
    assert not otp.verify("a@example.test", issued.code)
    assert not otp.verify(
        "a@example.test", issued.code, purpose="password_reset", binding="session-b"
    )
    assert otp.verify("a@example.test", issued.code, purpose="password_reset", binding="session-a")
    assert not otp.verify(
        "a@example.test", issued.code, purpose="password_reset", binding="session-a"
    )


def test_recovery_replaces_password_and_invalidates_other_sessions(
    client, customer, django_capture_on_commit_callbacks
):
    elsewhere = Client()
    elsewhere.force_login(customer)
    code = request_code(client)
    old_session = client.session.session_key
    with django_capture_on_commit_callbacks(execute=True):
        response = submit(client, code)
    assert response.status_code == 302
    customer.refresh_from_db()
    assert customer.check_password(PASSWORD)
    assert client.session.session_key != old_session
    assert client.get(reverse("account")).status_code == 200
    assert elsewhere.get(reverse("account")).status_code == 302
    assert "رمز عبور" in mail.outbox[-1].subject
    assert PASSWORD not in mail.outbox[-1].body
    assert submit(client, code).status_code == 200


def test_weak_password_does_not_consume_recovery_code(client, customer):
    code = request_code(client)
    assert submit(client, code, "123").status_code == 200
    assert submit(client, code).status_code == 302


def test_another_session_cannot_spend_recovery_code(client, customer):
    other = Client()
    other.force_login(customer)
    code = request_code(client)
    assert submit(other, code).status_code == 200
    assert submit(client, code).status_code == 302


def test_recovery_expires_at_ten_minutes(client, customer):
    with time_machine.travel("2026-10-29 12:00:00", tick=False) as clock:
        code = request_code(client)
        clock.shift(600)
        assert submit(client, code).status_code == 200
    customer.refresh_from_db()
    assert not customer.check_password(PASSWORD)


def test_recovery_does_not_accept_another_target_account(client, customer):
    other = User.objects.create_user("other@example.test", "unchanged-passphrase-420!")
    code = request_code(client)
    response = client.post(
        reverse("account-password-recovery"),
        {
            "action": "change",
            "email": other.email,
            "user_id": other.pk,
            "code": code,
            "new_password1": PASSWORD,
            "new_password2": PASSWORD,
        },
    )
    assert response.status_code == 302
    other.refresh_from_db()
    assert not other.check_password(PASSWORD)


def test_recovery_request_uses_existing_request_budget(client, customer):
    for _ in range(3):
        request_code(client)
    before = len(mail.outbox)
    response = client.post(reverse("account-password-recovery"), {"action": "request"})
    assert response.status_code == 429
    assert len(mail.outbox) == before


def test_recovery_requires_authentication(client):
    response = client.get(reverse("account-password-recovery"))
    assert response.status_code == 302


def test_password_change_cannot_overwrite_a_concurrent_reset(client, customer, monkeypatch):
    from apps.accounts.forms import StyledPasswordChangeForm

    original = StyledPasswordChangeForm.is_valid

    def valid_then_concurrent_reset(form):
        valid = original(form)
        changed = User.objects.get(pk=customer.pk)
        changed.set_password("concurrent-passphrase-827!")
        changed.save(update_fields=["password"])
        return valid

    monkeypatch.setattr(StyledPasswordChangeForm, "is_valid", valid_then_concurrent_reset)
    response = client.post(
        reverse("account-password"),
        {
            "old_password": "previous-passphrase-420!",
            "new_password1": PASSWORD,
            "new_password2": PASSWORD,
        },
    )
    assert response.status_code == 403
    customer.refresh_from_db()
    assert customer.check_password("concurrent-passphrase-827!")


def test_service_validates_password_before_spending_code(client, customer):
    from importlib import import_module

    from django.conf import settings
    from django.test import RequestFactory

    code = request_code(client)
    request = RequestFactory().post("/")
    request.user = customer
    request.session = import_module(settings.SESSION_ENGINE).SessionStore(
        client.session.session_key
    )
    with pytest.raises(ValidationError):
        services.complete_password_recovery(request, code=code, password="123")  # noqa: S106
    assert submit(client, code).status_code == 302


def test_notice_failure_cannot_undo_reset_or_log_secrets(
    client, customer, monkeypatch, caplog, django_capture_on_commit_callbacks
):
    import smtplib

    code = request_code(client)
    original = services.send_templated_email

    def fail_notice(name, **kwargs):
        if name == "password_changed":
            raise smtplib.SMTPRecipientsRefused({customer.email: (550, PASSWORD)})
        return original(name, **kwargs)

    monkeypatch.setattr(services, "send_templated_email", fail_notice)
    with django_capture_on_commit_callbacks(execute=True):
        assert submit(client, code).status_code == 302
    customer.refresh_from_db()
    assert customer.check_password(PASSWORD)
    assert "password change notice not sent" in caplog.text
    assert all(secret not in caplog.text for secret in [customer.email, PASSWORD, code])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_session", [True, False])
def test_simultaneous_recoveries_serialize_on_postgresql(
    client, customer, monkeypatch, same_session
):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from importlib import import_module
    from queue import Queue
    from threading import Event

    from django.conf import settings
    from django.core.exceptions import PermissionDenied
    from django.db import connection
    from django.test import RequestFactory

    first_code = request_code(client)
    other = Client()
    other.force_login(customer)
    second_code = first_code if same_session else request_code(other)
    first_key = client.session.session_key
    second_key = first_key if same_session else other.session.session_key
    entered, release = Event(), Event()
    pids = Queue()
    original = otp.verify

    def held_verify(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(otp, "verify", held_verify)

    def recover(key, code):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
                cursor.execute("SET statement_timeout = '12s'")
            request = RequestFactory().post("/")
            request.user = User.objects.get(pk=customer.pk)
            request.session = import_module(settings.SESSION_ENGINE).SessionStore(key)
            try:
                return services.complete_password_recovery(request, code=code, password=PASSWORD)
            except PermissionDenied:
                return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recover, first_key, first_code)
        first_pid = pids.get(timeout=10)
        assert entered.wait(10)
        second = pool.submit(recover, second_key, second_code)
        second_pid = pids.get(timeout=10)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [second_pid])
                    if first_pid in cursor.fetchone()[0]:
                        break
                time.sleep(0.01)
            else:
                pytest.fail("The second recovery did not wait for the first database lock.")
        finally:
            release.set()
        assert first.result(timeout=10).pk == customer.pk
        assert second.result(timeout=10) is None
    customer.refresh_from_db()
    assert customer.check_password(PASSWORD)


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("javascript,width", [(True, 390), (False, 1440)])
def test_recovery_browser_with_html_fallback(
    browser, live_server, client, customer, javascript, width
):
    from playwright.sync_api import expect

    page = browser.new_page(
        java_script_enabled=javascript, viewport={"width": width, "height": 900}
    )
    try:
        page.context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": client.cookies["sessionid"].value,
                    "url": live_server.url,
                }
            ]
        )
        page.goto(live_server.url + reverse("account-password-recovery"))
        expect(page.get_by_role("heading", name="رمز عبور جدید")).to_be_visible()
        page.get_by_role("button", name="فرستادن کد جدید").click()
        text = mail.outbox[-1].body.translate(otp.PERSIAN_TO_ASCII_DIGITS)
        code = re.search(r"\b[0-9]{6}\b", text).group()
        page.get_by_label("کد بازیابی").fill(code)
        page.locator('[name="new_password1"]').fill("123")
        page.locator('[name="new_password2"]').fill("123")
        page.get_by_role("button", name="ثبت رمز عبور جدید").click()
        expect(page.locator("#id_new_password1")).to_have_attribute("aria-invalid", "true")
        page.locator('[name="new_password1"]').fill(PASSWORD)
        page.locator('[name="new_password2"]').fill(PASSWORD)
        page.get_by_role("button", name="ثبت رمز عبور جدید").click()
        expect(page).to_have_url(live_server.url + reverse("account-password"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        customer.refresh_from_db()
        assert customer.check_password(PASSWORD)
    finally:
        page.close()


def test_password_change_accepts_an_older_password_hash(client, customer, settings):
    from django.contrib.auth.hashers import PBKDF2PasswordHasher

    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
    customer.password = PBKDF2PasswordHasher().encode("previous-passphrase-420!", "old-salt", 1)
    customer.save(update_fields=["password"])
    client.force_login(customer)
    response = client.post(
        reverse("account-password"),
        {
            "old_password": "previous-passphrase-420!",
            "new_password1": PASSWORD,
            "new_password2": PASSWORD,
        },
    )
    assert response.status_code == 302
    customer.refresh_from_db()
    assert customer.check_password(PASSWORD)
    assert client.get(reverse("account")).status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("surface", ["storefront", "panel"])
def test_login_hash_upgrade_cannot_overwrite_successful_recovery(
    client, customer, settings, monkeypatch, surface
):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from importlib import import_module
    from queue import Queue
    from threading import Event

    from django.contrib.auth.hashers import PBKDF2PasswordHasher
    from django.contrib.auth.models import AnonymousUser
    from django.core.exceptions import PermissionDenied
    from django.db import connection
    from django.test import RequestFactory

    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
    old_hash = PBKDF2PasswordHasher().encode("previous-passphrase-420!", "old-salt", 1)
    customer.password = old_hash
    customer.is_staff = surface == "panel"
    customer.save(update_fields=["password", "is_staff"])
    client.force_login(customer)
    code = request_code(client)
    key = client.session.session_key
    entered, release = Event(), Event()
    pids = Queue()
    original = PBKDF2PasswordHasher.verify

    def held_verify(hasher, password, encoded):
        if encoded == old_hash:
            entered.set()
            assert release.wait(15)
        return original(hasher, password, encoded)

    monkeypatch.setattr(PBKDF2PasswordHasher, "verify", held_verify)

    def compete(recovering):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
                cursor.execute("SET statement_timeout = '20s'")
            request = RequestFactory().post("/")
            request.user = User.objects.get(pk=customer.pk) if recovering else AnonymousUser()
            request.session = import_module(settings.SESSION_ENGINE).SessionStore(
                key if recovering else None
            )
            if recovering:
                try:
                    return services.complete_password_recovery(
                        request, code=code, password=PASSWORD
                    )
                except PermissionDenied:
                    return None
            if surface == "panel":
                return Client().post(
                    reverse("admin:login"),
                    {
                        "username": customer.email,
                        "password": "previous-passphrase-420!",
                    },
                )
            return services.login_with_password(request, customer.email, "previous-passphrase-420!")
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        login = pool.submit(compete, False)
        login_pid = pids.get(timeout=10)
        assert entered.wait(10)
        recovery = pool.submit(compete, True)
        recovery_pid = pids.get(timeout=10)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if recovery.done():
                    break
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [recovery_pid])
                    if login_pid in cursor.fetchone()[0]:
                        break
                time.sleep(0.01)
            else:
                pytest.fail("Neither recovery completion nor a password lock was observed.")
        finally:
            release.set()
        login.result(timeout=15)
        recovered = recovery.result(timeout=15)
    customer.refresh_from_db()
    if recovered is not None:
        assert customer.check_password(
            PASSWORD
        ), "A successful reset must remain the stored password."
    else:
        assert client.get(reverse("account")).status_code == 302

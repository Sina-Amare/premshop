"""Email after phone sign-in: an optional address for order mail, asked at checkout."""

from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.notifications import services as notifications
from apps.notifications.models import Notification
from apps.orders.models import Order
from apps.orders.services import OrderLine, place_order
from apps.payments import services

pytestmark = pytest.mark.django_db


@pytest.fixture
def phone_only(plan):
    account = User.objects.create_user(phone="09123456789")
    cart = Cart.objects.create(user=account)
    CartItem.objects.create(cart=cart, plan=plan, quantity=1)
    return account


def check_out(account, **changes):
    data = {
        "user": account,
        "quote": services.quote_checkout(user=account).token,
        "checkout_id": uuid.uuid4(),
        "customer_inputs": {},
        **changes,
    }
    return services.checkout(**data)


def test_an_email_given_at_checkout_is_saved_and_used(phone_only):
    order = check_out(phone_only, email="Buyer@Example.test")

    phone_only.refresh_from_db()
    assert order.placement_email == "buyer@example.test"
    assert phone_only.email == "buyer@example.test"


def test_an_order_without_an_email_sends_its_telegram_notice_and_no_email(phone_only, settings):
    settings.TELEGRAM_BOT_TOKEN = "123:test"  # noqa: S105  # nosec B105 - a test token
    User.objects.filter(pk=phone_only.pk).update(telegram_id=4242)
    order = check_out(phone_only)

    notifications.enqueue_event("payment_confirmed", 1, order.pk)

    assert order.placement_email == ""
    rows = Notification.objects.filter(order=order).exclude(template_name="purchase_confirmation")
    assert [row.channel for row in rows] == [Notification.Channel.TELEGRAM]


def test_the_checkout_asks_for_an_optional_email_instead_of_a_phone(client, phone_only):
    client.force_login(phone_only)

    body = client.get(reverse("checkout")).content.decode()

    assert 'name="email"' in body and 'name="phone"' not in body
    assert "۰۹۱۲۳۴۵۶۷۸۹" in body


def test_the_account_page_shows_the_number_and_edits_the_email(client, phone_only):
    client.force_login(phone_only)

    client.post(reverse("account"), {"email": "New@Example.test"})

    phone_only.refresh_from_db()
    assert phone_only.email == "new@example.test" and phone_only.phone == "09123456789"
    assert "۰۹۱۲۳۴۵۶۷۸۹" in client.get(reverse("account")).content.decode()


def test_an_account_with_no_number_never_reads_none(client, plan):
    # Made before the number was the identity, so it has none.
    account = User.objects.create_user("legacy@example.test")
    cart = Cart.objects.create(user=account)
    CartItem.objects.create(cart=cart, plan=plan, quantity=1)
    client.force_login(account)

    for url in (reverse("checkout"), reverse("account")):
        page = client.get(url)
        assert page.status_code == 200
        assert "None" not in page.content.decode(), url


@pytest.mark.parametrize("email", ["a" * 250 + "@example.test", 42])
def test_the_services_refuse_an_email_the_form_would(phone_only, plan, email):
    with pytest.raises(ValidationError):
        place_order(user=phone_only, lines=[OrderLine(plan, 1, None)], email=email)
    with pytest.raises(ValidationError):
        check_out(phone_only, email=email)


def test_checkout_says_a_typed_email_is_kept_on_an_account_without_one(client, phone_only):
    client.force_login(phone_only)

    page = client.get(reverse("checkout")).content.decode()

    assert "اگر برای حساب کاربری ایمیلی ثبت نکرده‌اید، این نشانی در حساب هم ذخیره می‌شود." in page


GMAIL_ERROR = "ایمیل Gmail را کامل و به شکل name@gmail.com وارد کنید."


def checkout_post(client, buyer, **data):
    client.force_login(buyer)
    return client.post(
        reverse("checkout"),
        {
            "action": "checkout",
            "quote": services.quote_checkout(user=buyer).token,
            "checkout_id": uuid.uuid4(),
            **data,
        },
    )


@pytest.mark.parametrize("email", ["buyer@outlook.com", "missing-at"])
def test_gm_checkout_rejects_the_address_in_its_own_field(client, phone_only, email):
    response = checkout_post(client, phone_only, email=email)

    assert response.status_code == 400
    assert response.context["form"].errors == {"email": [GMAIL_ERROR]}
    body = response.content.decode()
    assert f'<div id="id_email_error"><p class="field__error">{GMAIL_ERROR}</p>' in body
    assert body.count(GMAIL_ERROR) == 1
    phone_only.refresh_from_db()
    assert phone_only.email == "" and not Order.objects.exists()
    assert CartItem.objects.filter(cart__user=phone_only).exists()


def test_gm_place_order_rejects_a_non_gmail_address(phone_only, plan):
    with pytest.raises(ValidationError) as invalid:
        place_order(user=phone_only, lines=[OrderLine(plan, 1, None)], email="buyer@outlook.com")

    assert invalid.value.messages == [GMAIL_ERROR]
    phone_only.refresh_from_db()
    assert phone_only.email == "" and not Order.objects.exists()


@pytest.mark.parametrize("email", ["other@outlook.com", "missing-at"])
def test_gm_account_rejects_the_address_in_its_own_field(client, phone_only, email):
    phone_only.email = "saved@gmail.com"
    phone_only.save(update_fields=["email"])
    client.force_login(phone_only)
    response = client.post(reverse("account"), {"email": email})

    assert response.status_code == 200
    assert response.context["profile_form"].errors == {"email": [GMAIL_ERROR]}
    body = response.content.decode()
    assert f'<div id="id_email_error"><p class="field__error">{GMAIL_ERROR}</p>' in body
    assert body.count(GMAIL_ERROR) == 1
    phone_only.refresh_from_db()
    assert phone_only.email == "saved@gmail.com"


def test_gm_account_accepts_blank(client, phone_only):
    phone_only.email = "saved@gmail.com"
    phone_only.save(update_fields=["email"])
    client.force_login(phone_only)

    assert client.post(reverse("account"), {"email": ""}).status_code == 302
    phone_only.refresh_from_db()
    assert phone_only.email == ""


def test_gm_checkout_accepts_blank_and_an_outlook_activation_address(client, phone_only, plan):
    plan.input_type = "email"
    plan.max_quantity = 1
    plan.save()
    response = checkout_post(
        client, phone_only, email="", **{f"customer_input_{plan.pk}": "login@outlook.com"}
    )

    assert response.status_code == 302
    order = Order.objects.get(user=phone_only)
    assert order.placement_email == ""
    assert order.items.get().customer_input == "login@outlook.com"

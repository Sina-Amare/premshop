"""Checkout contact and the address kept for delivery."""

from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

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


def link_telegram(account):
    User.objects.filter(pk=account.pk).update(telegram_id=4242, telegram_linked_at=timezone.now())


def test_an_email_given_at_checkout_is_saved_and_used(client, phone_only):
    response = checkout_post(client, phone_only, email="Buyer+Orders@Gmail.com")

    phone_only.refresh_from_db()
    assert response.status_code == 302
    order = Order.objects.get(user=phone_only)
    assert order.placement_email == "buyer+orders@gmail.com"
    assert phone_only.email == "buyer+orders@gmail.com"


def test_an_order_without_an_email_sends_its_telegram_notice_and_no_email(phone_only, settings):
    settings.TELEGRAM_BOT_TOKEN = "123:test"  # noqa: S105  # nosec B105 - a test token
    link_telegram(phone_only)
    order = check_out(phone_only)

    notifications.enqueue_event("payment_confirmed", 1, order.pk)

    assert order.placement_email == ""
    rows = Notification.objects.filter(order=order).exclude(template_name="purchase_confirmation")
    assert [row.channel for row in rows] == [Notification.Channel.TELEGRAM]


def test_the_checkout_requires_email_instead_of_a_phone_for_an_unreachable_account(
    client, phone_only
):
    client.force_login(phone_only)

    body = client.get(reverse("checkout")).content.decode()

    assert 'name="email"' in body and 'name="phone"' not in body
    assert "۰۹۱۲۳۴۵۶۷۸۹" in body
    assert '<label class="field__label" for="id_email">ایمیل Gmail</label>' in body


def test_the_account_page_shows_the_number_and_edits_the_email(client, phone_only):
    client.force_login(phone_only)

    client.post(reverse("account"), {"email": "New@Gmail.com"})

    phone_only.refresh_from_db()
    assert phone_only.email == "new@gmail.com" and phone_only.phone == "09123456789"
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
REQUIRED_ERROR = (
    "ایمیل Gmail خود را وارد کنید. برای دیدن اطلاعات تحویل، کد را به همین ایمیل می‌فرستیم."
)


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


def test_unreachable_account_gets_one_error_on_a_blank_checkout_email(client, phone_only):
    response = checkout_post(client, phone_only, email="")

    assert response.status_code == 400
    assert response.context["form"].errors == {"email": [REQUIRED_ERROR]}
    assert not response.context["error"]
    body = response.content.decode()
    assert f'<div id="id_email_error"><p class="field__error">{REQUIRED_ERROR}</p>' in body
    assert body.count(REQUIRED_ERROR) == 1
    assert not Order.objects.exists()
    assert CartItem.objects.get(cart__user=phone_only).quantity == 1


def test_checkout_form_requires_a_blank_email_to_be_filled(client, phone_only):
    client.force_login(phone_only)
    form = client.get(reverse("checkout")).context["form"]

    with pytest.raises(ValidationError) as invalid:
        form.fields["email"].clean("")

    assert invalid.value.messages == [REQUIRED_ERROR]


@pytest.mark.parametrize("telegram_contact", ["neither", "id_only", "date_only"])
def test_place_order_rejects_blank_without_email_or_complete_telegram_link(
    phone_only, plan, telegram_contact
):
    User.objects.filter(pk=phone_only.pk).update(
        telegram_id=4242 if telegram_contact == "id_only" else None,
        telegram_linked_at=timezone.now() if telegram_contact == "date_only" else None,
    )
    with pytest.raises(ValidationError) as invalid:
        place_order(user=phone_only, lines=[OrderLine(plan, 1)], email="  ")

    assert invalid.value.messages == [REQUIRED_ERROR]
    assert not Order.objects.exists()
    assert CartItem.objects.get(cart__user=phone_only).quantity == 1


@pytest.mark.parametrize("saved_email", ["saved@gmail.com", ""])
def test_reachable_account_has_an_optional_checkout_email(client, phone_only, saved_email):
    if saved_email:
        User.objects.filter(pk=phone_only.pk).update(email=saved_email)
    else:
        link_telegram(phone_only)
    client.force_login(phone_only)

    page = client.get(reverse("checkout"))
    assert (
        '<label class="field__label" for="id_email">ایمیل Gmail (اختیاری)</label>'
        in page.content.decode()
    )
    assert not page.context["form"].fields["email"].required
    assert checkout_post(client, phone_only, email="").status_code == 302
    assert Order.objects.get(user=phone_only).placement_email == saved_email


def test_place_order_replays_a_blank_email_after_the_account_loses_contact(phone_only, plan):
    User.objects.filter(pk=phone_only.pk).update(email="saved@gmail.com")
    data = {
        "user": phone_only,
        "lines": [OrderLine(plan, 1)],
        "checkout_id": uuid.uuid4(),
        "submission_digest": "a" * 64,
    }
    order = place_order(**data)
    User.objects.filter(pk=phone_only.pk).update(email="")

    assert place_order(**data) == order
    assert Order.objects.count() == 1


def test_checkout_replays_a_blank_email_after_the_account_loses_contact(client, phone_only):
    link_telegram(phone_only)
    client.force_login(phone_only)
    url = reverse("checkout")
    data = {
        "action": "checkout",
        "quote": services.quote_checkout(user=phone_only).token,
        "checkout_id": uuid.uuid4(),
        "email": "",
    }
    first = client.post(url, data)
    assert first.status_code == 302
    User.objects.filter(pk=phone_only.pk).update(telegram_id=None, telegram_linked_at=None)

    replay = client.post(url, data)
    assert replay.status_code == 302 and replay.url == first.url
    assert Order.objects.count() == 1


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
    link_telegram(phone_only)
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

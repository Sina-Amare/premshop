import re
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.test import Client

from apps.cart.models import Cart, CartItem
from apps.cart.services import GUEST_CART_ID

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("login", ["password", "code"])
def test_real_login_rotates_session_keeps_cart_and_restores_auth_lifetime(
    client, buyer, plan, login
):
    client.post("/cart/add/", {"plan": plan.pk, "quantity": 2})
    guest = Cart.objects.get()
    guest_key, guest_id = client.session.session_key, guest.pk
    if login == "password":
        response = client.post(
            "/login/",
            {"email": buyer.email, "password": "CartPassword!2026", "next": "/cart/"},
        )
    else:
        client.post("/login/code/", {"email": buyer.email, "next": "/cart/"})
        digits = re.search(r"[۰-۹]{6}", mail.outbox[-1].body).group()
        code = digits.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        response = client.post("/login/code/verify/", {"code": code})
    assert response.status_code == 302
    assert response.url == "/cart/"
    assert client.session.session_key != guest_key
    assert GUEST_CART_ID not in client.session
    assert client.session.get_expiry_age() == settings.SESSION_COOKIE_AGE
    guest.refresh_from_db()
    assert guest.user_id == buyer.pk and guest.session_key is None
    assert guest.pk == guest_id and guest.items.get().quantity == 2


def test_password_login_combines_existing_cart_and_shows_reduction(client, buyer, plan):
    account = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=account, plan=plan, quantity=2)
    client.post("/cart/add/", {"plan": plan.pk, "quantity": 2})
    response = client.post(
        "/login/",
        {"email": buyer.email, "password": "CartPassword!2026", "next": "/cart/"},
        follow=True,
    )
    assert "سقف مجاز" in response.content.decode()
    assert account.items.get().quantity == 3
    assert Cart.objects.count() == 1


def test_browser_restart_restores_guest_cart_but_other_browser_cannot_read_it(client, plan):
    response = client.post("/cart/add/", {"plan": plan.pk, "quantity": 2})
    assert response.cookies[settings.SESSION_COOKIE_NAME]["max-age"] == 30 * 86400
    restarted = Client()
    restarted.cookies[settings.SESSION_COOKIE_NAME] = client.cookies[
        settings.SESSION_COOKIE_NAME
    ].value
    assert restarted.get("/cart/").context["cart"].lines[0].quantity == 2
    assert not Client().get("/cart/").context["cart"].lines


def test_existing_custom_session_expiry_survives_guest_cart_and_login(client, buyer, plan):
    session = client.session
    session.set_expiry(3600)
    session.save()
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/login/", {"email": buyer.email, "password": "CartPassword!2026"})
    assert client.session.get_expiry_age() == 3600


def test_logout_does_not_expose_account_cart_to_the_next_visitor(client, buyer, plan):
    client.force_login(buyer)
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/logout/")
    assert not client.get("/cart/").context["cart"].lines
    assert CartItem.objects.get().quantity == 1


@pytest.mark.parametrize("login", ["password", "code", "admin"])
def test_failed_merge_leaves_the_original_guest_session_usable(client, buyer, plan, login):
    client.post("/cart/add/", {"plan": plan.pk})
    original_key = client.session.session_key
    if login == "admin":
        buyer.is_staff = True
        buyer.save(update_fields=["is_staff"])
        url, data = "/admin/login/", {"username": buyer.email, "password": "CartPassword!2026"}
    elif login == "password":
        url, data = "/login/", {"email": buyer.email, "password": "CartPassword!2026"}
    else:
        client.post("/login/code/", {"email": buyer.email})
        digits = re.search(r"[۰-۹]{6}", mail.outbox[-1].body).group()
        url, data = "/login/code/verify/", {"code": digits}
    with patch("apps.cart.signals.merge_carts", side_effect=RuntimeError("merge failed")):
        with pytest.raises(RuntimeError):
            client.post(url, data)
    assert client.session.session_key == original_key
    assert client.session[GUEST_CART_ID] == Cart.objects.get().pk
    assert client.get("/cart/").context["cart"].lines[0].quantity == 1

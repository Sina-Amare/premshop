import pytest
from django.test import Client

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def test_empty_cart_is_visible_without_creating_a_session(client):
    response = client.get("/cart/")
    assert response.status_code == 200
    assert "سبد خرید شما خالی است" in response.content.decode()
    assert "sessionid" not in response.cookies


def test_cart_mutations_require_post(client):
    for action in ("add", "quantity", "remove"):
        assert client.get(f"/cart/{action}/").status_code == 405


def test_prices_owner_and_cart_id_from_browser_are_ignored(client, plan, buyer):
    other = Cart.objects.create(user=buyer)
    response = client.post(
        "/cart/add/",
        {
            "plan": plan.pk,
            "quantity": 2,
            "price": 1,
            "user": buyer.pk,
            "cart": other.pk,
        },
    )
    assert response.status_code == 302 and response.url == "/cart/"
    assert not other.items.exists()
    page = client.get("/cart/")
    assert page.context["cart"].total == 400000
    assert "no-store" in page["Cache-Control"]


@pytest.mark.parametrize(
    "data", [{}, {"plan": "invalid"}, {"plan": 1, "quantity": "1.5"}, {"plan": 1, "quantity": -1}]
)
def test_bad_input_never_changes_cart(client, data):
    assert client.post("/cart/add/", data).status_code == 400
    assert not Cart.objects.exists()


def test_hidden_product_facts_never_reappear_in_a_cart(client, plan):
    client.post("/cart/add/", {"plan": plan.pk})
    Product.objects.filter(pk=plan.product_id).update(
        status=Product.Status.DRAFT, name="private-draft-name"
    )
    body = client.get("/cart/").content.decode()
    assert "private-draft-name" not in body
    assert plan.title not in body
    assert "در دسترس نیست" in body
    assert client.post("/cart/remove/", {"plan": plan.pk}).status_code == 302
    assert not CartItem.objects.exists()


def test_csrf_is_required_for_every_mutation(plan):
    client = Client(enforce_csrf_checks=True)
    for action in ("add", "quantity", "remove"):
        assert client.post(f"/cart/{action}/", {"plan": plan.pk, "quantity": 1}).status_code == 403
    assert not Cart.objects.exists()


def test_available_product_button_posts_to_cart(client, plan):
    body = client.get(plan.product.get_absolute_url()).content.decode()
    assert 'action="/cart/add/"' in body
    assert "data-cart-pending" not in body
    assert 'class="btn btn--primary buybox__cta" disabled' not in body


def test_quantity_step_buttons_work_without_javascript(client, plan):
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/quantity/", {"plan": plan.pk, "quantity": 1, "step": 1})
    assert CartItem.objects.get().quantity == 2
    client.post("/cart/quantity/", {"plan": plan.pk, "quantity": 2, "step": -1})
    assert CartItem.objects.get().quantity == 1
    assert (
        client.post("/cart/quantity/", {"plan": plan.pk, "quantity": 1, "step": 99}).status_code
        == 400
    )

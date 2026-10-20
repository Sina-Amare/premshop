"""A draft never reaches a visitor, signed in or not, by any road.

Every visitor-facing query goes through `Product.objects.public()`. These
tests hold each road a product can take onto a page: the home page, its
department, a search for it, a search miss, the suggestions under
another product, and its own URL. The one exception is the operator's
preview: a draft's own address opens for staff who may view products, marked
as a draft, kept out of the index and out of every cache. `?preview=1` is
tried on every road, so no parameter opens one for anyone else.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product

pytestmark = pytest.mark.django_db

DRAFT_NAME = "Draftonly"


def make(category: Category, slug: str, status: str) -> Product:
    product = Product.objects.create(
        category=category,
        name=DRAFT_NAME if status == Product.Status.DRAFT else slug,
        slug=slug,
        short_description="",
        # Searched for by this word, because the page echoes the query back
        # and the name in the echo would look like a leak.
        aliases="secretalias" if status == Product.Status.DRAFT else "",
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=status,
    )
    Plan.objects.create(
        product=product,
        title="۱ ماهه",
        cost_price=Decimal(1),
        sale_price=Decimal(100000),
        promo_price=Decimal(90000),
    )
    return product


@pytest.fixture
def shop():
    """A live product beside a draft in its own department, which is where a
    suggestion and a department listing would find it first."""
    category = Category.objects.create(name="سرگرمی", slug="fun")
    live = make(category, "live", Product.Status.ACTIVE)
    make(category, "draft", Product.Status.DRAFT)
    # So the suggestions row under «live» renders at all.
    make(Category.objects.create(name="ابزار", slug="tools"), "other", Product.Status.ACTIVE)
    # The draft's department exists only for the draft: it must not appear either.
    hidden = Category.objects.create(name="Hiddendept", slug="hidden")
    make(hidden, "draft-two", Product.Status.DRAFT)
    return live


ROADS = {
    "home": lambda: reverse("home"),
    "department": lambda: reverse("category", args=["fun"]),
    "search for it": lambda: reverse("search") + "?q=secretalias",
    "search miss": lambda: reverse("search") + "?q=zzzz",
    "suggestions": lambda: reverse("product", args=["live"]),
    "faq": lambda: reverse("faq"),
}


def operator() -> User:
    user = User.objects.create_user("op@example.test", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_product"))
    return user


@pytest.fixture(params=["anonymous", "customer", "staff", "operator"])
def visitor(request, client):
    if request.param == "customer":
        client.force_login(User.objects.create_user("buyer@example.test", is_verified=True))
    if request.param == "staff":
        # Staff who may not view products in the panel see no more of them here.
        client.force_login(User.objects.create_user("staff@example.test", is_staff=True))
    if request.param == "operator":
        client.force_login(operator())
    return client


@pytest.mark.parametrize("preview", ["", "preview=1"])
@pytest.mark.parametrize("road", ROADS)
def test_a_draft_is_on_no_page(visitor, shop, road, preview):
    url = ROADS[road]()
    url += ("&" if "?" in url else "?") + preview if preview else ""

    response = visitor.get(url)
    body = response.content.decode()

    assert response.status_code == 200, url
    assert DRAFT_NAME not in body, url
    assert "/p/draft" not in body, url
    assert "Hiddendept" not in body, url


@pytest.mark.parametrize("visitor", ["anonymous", "customer", "staff"], indirect=True)
@pytest.mark.parametrize("preview", ["", "?preview=1"])
@pytest.mark.parametrize("slug", ["draft", "draft-two"])
def test_a_draft_has_no_address(visitor, shop, slug, preview):
    assert visitor.get(reverse("product", args=[slug]) + preview).status_code == 404


def test_the_operator_previews_a_draft_at_its_own_address(client, shop):
    draft = Product.objects.get(slug="draft")
    client.force_login(operator())

    response = client.get(draft.get_absolute_url())
    body = response.content.decode()

    assert response.status_code == 200
    assert DRAFT_NAME in body
    assert 'class="badge badge--key">پیش‌نویس</span>' in body
    assert '<meta name="robots" content="noindex">' in body
    assert 'rel="canonical"' not in body
    # The same address will be the public page: no cache may keep this one.
    assert {"private", "no-store"} <= {
        part.strip() for part in response["Cache-Control"].split(",")
    }


def test_a_published_page_is_not_a_preview_even_for_the_operator(client, shop):
    client.force_login(operator())

    response = client.get(reverse("product", args=["live"]))

    assert "پیش‌نویس" not in response.content.decode()
    assert "no-store" not in response.get("Cache-Control", "")


def test_an_unavailable_product_stays_hidden_even_from_the_operator(client, shop):
    """Only a draft is previewed; «ناموجود» is a product taken off the shelf."""
    Product.objects.filter(slug="draft").update(status=Product.Status.UNAVAILABLE)
    client.force_login(operator())

    assert client.get(reverse("product", args=["draft"])).status_code == 404


def test_the_same_roads_carry_a_published_product(client, shop):
    """The control. A road that shows no product at all would pass the tests
    above without guarding anything."""
    for road in ("home", "department", "search miss"):
        assert "/p/live/" in client.get(ROADS[road]()).content.decode(), road
    assert "/p/other/" in client.get(ROADS["suggestions"]()).content.decode()
    assert "/p/live/" in client.get(reverse("search") + "?q=live").content.decode()
    assert client.get(reverse("product", args=["live"])).status_code == 200

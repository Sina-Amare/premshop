from decimal import Decimal

import pytest
from django.test import Client

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Plan, Product

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
    assert 'class="cart-line__stock">ناموجود</p>' in body
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


def test_add_json_stays_on_product_and_reports_authoritative_count_and_limits(client, plan):
    from apps.catalog.models import Plan

    other = Plan.objects.create(
        product=plan.product, title="سه ماهه", cost_price=400_000, sale_price=500_000
    )
    response = client.post(
        "/cart/add/", {"plan": plan.pk, "quantity": 2}, HTTP_ACCEPT="application/json"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["options"][str(plan.pk)]["max_add"] == 1
    assert data["options"][str(other.pk)]["selectable"] is True
    assert data["message"] == f"{plan.product.name}، {plan.title} به سبد خرید اضافه شد."


def test_stock_json_failure_leaves_cart_unchanged_and_uses_plain_persian(client, plan):
    plan.stock_quantity = 0
    plan.save()
    response = client.post("/cart/add/", {"plan": plan.pk}, HTTP_ACCEPT="application/json")
    assert response.status_code == 400
    assert response.json()["message"] == "این محصول فعلاً موجود نیست."
    assert not Cart.objects.exists()


def test_cart_uses_one_clear_unavailable_message_without_accounting_jargon(client, plan):
    client.post("/cart/add/", {"plan": plan.pk})
    type(plan).objects.filter(pk=plan.pk).update(is_available=False)
    body = client.get("/cart/").content.decode()
    assert 'class="cart-line__stock">ناموجود</p>' in body
    assert "خارج از" not in body and "مبلغ این قلم" not in body


def test_availability_is_private_current_and_does_not_reveal_drafts(client, plan):
    plan.stock_quantity = 2
    plan.save()
    client.post("/cart/add/", {"plan": plan.pk})
    stranger = Client()
    state = stranger.get("/cart/availability/", {"plan": plan.pk})
    assert state.json()["options"][str(plan.pk)]["max_add"] == 2
    assert state.json()["count"] == 0
    assert "no-store" in state["Cache-Control"]
    assert "sessionid" not in state.cookies
    Product.objects.filter(pk=plan.product_id).update(status=Product.Status.DRAFT)
    assert stranger.get("/cart/availability/", {"plan": plan.pk}).json()["options"] == {}


@pytest.mark.parametrize("plans", [["x"], ["-1"], ["1"] * 51, ["9" * 40]])
def test_availability_bounds_browser_inputs(client, plans):
    assert client.get("/cart/availability/", {"plan": plans}).status_code == 400


def test_json_add_still_requires_csrf(plan):
    response = Client(enforce_csrf_checks=True).post(
        "/cart/add/", {"plan": plan.pk}, HTTP_ACCEPT="application/json"
    )
    assert response.status_code == 403
    assert not Cart.objects.exists()


def test_add_json_names_the_item_apart_from_the_status(client, plan):
    data = client.post("/cart/add/", {"plan": plan.pk}, HTTP_ACCEPT="application/json").json()
    assert (data["title"], data["item"], data["option"], data["image"]) == (
        "به سبد خرید اضافه شد",
        plan.product.name,
        plan.title,
        "",
    )


def test_an_empty_cart_hides_the_header_count(client):
    body = client.get("/cart/").content.decode()
    assert '<span class="v-cart__count" aria-hidden="true" hidden>' in body


def test_cart_summary_links_to_checkout(client, plan):
    client.post("/cart/add/", {"plan": plan.pk})
    body = client.get("/cart/").content.decode()
    assert 'href="/checkout/">ادامه و ثبت سفارش</a>' in body


def test_cart_query_count_is_the_same_for_three_lines_and_nine(client, buyer, plan, queries_for):
    """Each line is a plan of its own product, and each product has a second plan
    and a promotion, so a per-line lookup of siblings or of a price would show."""
    cart = Cart.objects.create(user=buyer)
    client.force_login(buyer)

    def add(start, stop):
        for i in range(start, stop):
            product = Product.objects.create(
                category=plan.product.category,
                name=f"محصول {i}",
                slug=f"cart-{i}",
                status=Product.Status.ACTIVE,
            )
            Plan.objects.create(
                product=product,
                title="۱ ماهه",
                duration_days=30,
                cost_price=Decimal("1"),
                sale_price=Decimal("200000"),
                promo_price=Decimal("150000"),
                max_quantity=3,
            )
            item = Plan.objects.create(
                product=product,
                title="۳ ماهه",
                duration_days=90,
                cost_price=Decimal("1"),
                sale_price=Decimal("500000"),
                max_quantity=3,
            )
            CartItem.objects.create(cart=cart, plan=item, quantity=2)

    add(0, 3)
    few, response = queries_for("/cart/")
    assert len(response.context["cart"].lines) == 3
    add(3, 9)

    many, response = queries_for("/cart/")

    assert len(response.context["cart"].lines) == 9
    assert many == few


def _slower_plan(plan, hours, tag=""):
    product = Product.objects.create(
        category=plan.product.category,
        name=f"کند {hours}{tag}",
        slug=f"slow-{hours}{tag}",
        status=Product.Status.ACTIVE,
        delivery_hours=hours,
    )
    return Plan.objects.create(
        product=product,
        title="یک ماهه",
        duration_days=30,
        cost_price=Decimal("1"),
        sale_price=Decimal("200000"),
        max_quantity=3,
    )


def _summary_help(response):
    return response.content.decode().split('class="cart-summary__help"', 1)[1].split("</div>", 1)[0]


def test_cart_summary_promises_the_longest_time_among_its_own_lines(client, plan):
    Product.objects.filter(pk=plan.product.pk).update(delivery_hours=12)
    slower = _slower_plan(plan, 30)
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/add/", {"plan": slower.pk})

    page = client.get("/cart/")
    help_text = _summary_help(page)

    assert slower.product.delivery_sentence == "تحویل تا ۳۰ ساعت"
    assert "تحویل تا ۳۰ ساعت" in help_text
    assert "حداکثر ۴۸" not in help_text
    assert "تحویل تا ۱۲ ساعت" in page.content.decode()


def test_cart_summary_keeps_the_site_wide_sentence_when_a_line_has_no_time(client, plan):
    slower = _slower_plan(plan, 30)
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/add/", {"plan": slower.pk})
    Product.objects.filter(pk=slower.product.pk).update(status=Product.Status.DRAFT)

    help_text = _summary_help(client.get("/cart/"))

    assert "حداکثر ۴۸ ساعت پس از تأیید پرداخت" in help_text


def test_cart_summary_states_hours_from_the_longest_line_and_when_they_start(client, plan):
    slower = _slower_plan(plan, 30)
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/add/", {"plan": slower.pk})

    assert "تحویل تا ۳۰ ساعت پس از تأیید پرداخت" in _summary_help(client.get("/cart/"))


def test_a_delivery_note_never_becomes_the_cart_promise(client, plan):
    slower = _slower_plan(plan, 30)
    Product.objects.filter(pk=slower.product.pk).update(
        delivery_note="به‌علت مشکل پرداخت کندتر از معمول"
    )
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/add/", {"plan": slower.pk})

    page = client.get("/cart/")
    help_text = _summary_help(page)

    assert "تحویل تا ۳۰ ساعت پس از تأیید پرداخت" in help_text
    assert "کندتر از معمول" not in help_text
    assert "کندتر از معمول" in page.content.decode(), "the line keeps its own note"


def test_lines_tied_on_hours_give_the_same_summary_in_either_order(client, plan):
    first = _slower_plan(plan, 30)
    second = _slower_plan(plan, 30, tag="-b")
    Product.objects.filter(pk=first.product.pk).update(delivery_note="یادداشت اول")

    def summary(order):
        client.cookies.clear()
        for item in order:
            client.post("/cart/add/", {"plan": item.pk})
        return _summary_help(client.get("/cart/")).split("</p>", 1)[0]

    assert summary([first, second]) == summary([second, first])

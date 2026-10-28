from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from apps.cart.models import CartItem
from apps.cart.services import add_to_cart, cart_summary, get_cart, merge_carts
from apps.cart.tests.test_services import request_for
from apps.catalog.admin import stock_levels
from apps.catalog.models import Plan
from apps.core import support

pytestmark = pytest.mark.django_db


def test_adding_to_a_cart_does_not_reserve_the_last_unit(plan):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    first, second = request_for(), request_for()
    add_to_cart(first, plan_id=plan.pk)
    add_to_cart(second, plan_id=plan.pk)
    assert CartItem.objects.count() == 2
    assert cart_summary(get_cart(first)).total == plan.sale_price
    assert cart_summary(get_cart(second)).total == plan.sale_price


def test_cart_has_no_reservation_deadline_or_countdown(client, plan):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    data = client.post("/cart/add/", {"plan": plan.pk}, HTTP_ACCEPT="application/json").json()
    assert "reserved_until" not in {field.name for field in CartItem._meta.fields}
    assert "hold_message" not in data
    assert (
        not {"reserved_until", "expired", "reservation_required"}
        & data["options"][str(plan.pk)].keys()
    )
    with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(hours=2)):
        response = client.get("/cart/")
    assert response.context["cart"].total == plan.sale_price
    assert response.context["cart"].ready
    assert "رزرو" not in response.content.decode()


def test_stock_loss_keeps_the_item_with_restock_support_and_explicit_removal(client, plan):
    client.post("/cart/add/", {"plan": plan.pk})
    item_id = CartItem.objects.get().pk
    plan.stock_quantity = 0
    plan.save()
    response = client.get("/cart/")
    line = response.context["cart"].lines[0]
    assert line.unavailable and not response.context["cart"].ready
    assert CartItem.objects.get(pk=item_id).quantity == 1
    body = response.content.decode()
    assert "ناموجود" in body and "زمان موجود شدن دوباره را از پشتیبانی بپرسید." in body
    assert support.details()["telegram_url"] in body
    assert 'class="cart-remove"' in body
    plan.stock_quantity = 3
    plan.save()
    assert client.get("/cart/").context["cart"].ready
    assert CartItem.objects.get(pk=item_id).quantity == 1


def test_one_unavailable_line_blocks_readiness_without_losing_either_selection(client, plan):
    other = Plan.objects.create(
        product=plan.product, title="سه‌ماهه", cost_price=1, sale_price=500000
    )
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/add/", {"plan": other.pk})
    Plan.objects.filter(pk=other.pk).update(is_available=False)
    summary = client.get("/cart/").context["cart"]
    assert len(summary.lines) == 2 and not summary.ready
    assert summary.total == plan.sale_price
    client.post("/cart/remove/", {"plan": other.pk})
    assert client.get("/cart/").context["cart"].ready
    assert CartItem.objects.get().plan_id == plan.pk


def test_stock_edit_is_not_blocked_by_other_customers_carts(plan):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    add_to_cart(request_for(), plan_id=plan.pk, quantity=2)
    plan.refresh_from_db()
    plan.stock_quantity = 0
    plan.full_clean()
    plan.save()
    assert stock_levels(plan) == "۰ عدد قابل خرید"
    assert CartItem.objects.get().quantity == 2


def test_login_merge_keeps_intentions_when_stock_falls(plan, buyer):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    guest, account = request_for(), request_for(buyer)
    add_to_cart(guest, plan_id=plan.pk, quantity=2)
    add_to_cart(account, plan_id=plan.pk)
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    merge_carts(user=buyer, guest_cart_id=get_cart(guest).pk)
    summary = cart_summary(get_cart(account))
    assert summary.lines[0].quantity == 3
    assert summary.lines[0].quantity_invalid and not summary.ready


def test_empty_cart_is_not_ready(client):
    assert not client.get("/cart/").context["cart"].ready


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_quantity_changes_and_removal_update_in_place(browser, live_server, client, plan):
    other = Plan.objects.create(
        product=plan.product, title="سه‌ماهه", cost_price=1, sale_price=500000
    )
    client.post("/cart/add/", {"plan": plan.pk})
    client.post("/cart/add/", {"plan": other.pk})
    Plan.objects.filter(pk=other.pk).update(is_available=False)
    page = browser.new_page(viewport={"width": 390, "height": 900})
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
        page.goto(live_server.url + "/cart/")
        navigations = []
        page.on("framenavigated", lambda frame: navigations.append(frame.url))
        line = page.locator(f'[data-cart-plan="{plan.pk}"]')
        more = line.locator('[name=step][value="1"]')
        more.focus()
        page.keyboard.press("Enter")
        expect(line.locator("input[name=quantity]")).to_have_value("2")
        expect(more).to_be_focused()
        expect(line.locator(".cart-line__amount")).to_contain_text("۴۰۰,۰۰۰")
        expect(page.locator(".cart-summary__blocked")).to_be_visible()
        assert CartItem.objects.filter(plan=other).exists()
        page.locator(f'[data-cart-plan="{other.pk}"] .cart-remove').click()
        expect(page.locator(".cart-line")).to_have_count(1)
        expect(page.locator(".cart-summary__blocked")).to_have_count(0)
        assert not CartItem.objects.filter(plan=other).exists()
        assert not navigations
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_pending_cart_edit_keeps_focus_and_its_error_is_persistent(
    browser, live_server, client, plan
):
    client.post("/cart/add/", {"plan": plan.pk})
    page = browser.new_page()
    held = []
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
        page.goto(live_server.url + "/cart/")
        page.route("**/cart/quantity/", lambda route: held.append(route))
        more = page.locator('[name=step][value="1"]')
        more.focus()
        page.keyboard.press("Enter")
        expect(more).to_be_focused()
        more.evaluate("e=>{for(let n=0;n<10;n++)e.click()}")
        page.wait_for_timeout(150)
        assert len(held) == 1
        held[0].fulfill(
            status=400,
            content_type="application/json",
            body='{"message":"این محصول فعلاً موجود نیست."}',
        )
        expect(page.locator(".cart-line__error")).to_have_text("این محصول فعلاً موجود نیست.")
        expect(more).to_be_focused()
        page.clock.install()
        page.clock.fast_forward(8000)
        expect(page.locator(".cart-line__error")).to_be_visible()
        assert CartItem.objects.get().quantity == 1
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("later_edit", [None, "1", "2"])
def test_successful_step_accepts_server_quantity_but_keeps_later_edits(
    browser, live_server, client, plan, later_edit
):
    client.post("/cart/add/", {"plan": plan.pk})
    page = browser.new_page()
    held = []
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
        page.goto(live_server.url + "/cart/")
        page.route("**/cart/quantity/", lambda route: held.append(route))
        quantity = page.locator(".cart-quantity input[name=quantity]")
        quantity.fill("2")
        page.locator('[name=step][value="1"]').click()
        page.wait_for_timeout(100)
        assert len(held) == 1
        if later_edit is not None:
            quantity.fill("1")
            quantity.fill(later_edit)
        held[0].continue_()
        expect(page.locator(".cart-line__amount")).to_contain_text("۶۰۰,۰۰۰")
        expect(quantity).to_have_value(later_edit or "3")
        expect(page.locator(".cart-line__amount-label")).to_have_text("جمع ۳ عدد")
        if later_edit is not None:
            expect(page.locator("[data-cart-draft]")).to_be_visible()
        else:
            expect(page.locator("[data-cart-draft]")).to_be_hidden()
        assert CartItem.objects.get().quantity == 3
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("stored", [2, 3])
@pytest.mark.parametrize("javascript", [True, False])
def test_minus_corrects_a_selection_above_current_stock(
    browser, live_server, client, plan, stored, javascript
):
    client.post("/cart/add/", {"plan": plan.pk, "quantity": stored})
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    page = browser.new_page(java_script_enabled=javascript)
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
        page.goto(live_server.url + "/cart/")
        expect(page.locator(".cart-summary__blocked")).to_be_visible()
        page.locator(".cart-stepper button").filter(has_text="−").click()
        expect(page.locator(".cart-quantity input[name=quantity]")).to_have_value("1")
        expect(page.locator(".cart-line__amount")).to_contain_text("۲۰۰,۰۰۰")
        expect(page.locator(".cart-summary__blocked")).to_have_count(0)
        assert CartItem.objects.get().quantity == 1
    finally:
        page.close()

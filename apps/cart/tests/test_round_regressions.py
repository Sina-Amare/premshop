import pytest
from playwright.sync_api import expect

from apps.cart.models import CartItem
from apps.catalog.models import Plan, Product

pytestmark = pytest.mark.django_db


def test_unavailable_cart_and_polling_have_the_same_limit(client, plan):
    client.post("/cart/add/", {"plan": plan.pk})
    Plan.objects.filter(pk=plan.pk).update(is_available=False)
    line = client.get("/cart/").context["cart"].lines[0]
    state = client.get("/cart/availability/", {"plan": plan.pk}).json()["options"][str(plan.pk)]
    assert line.max_quantity == state["maximum"] == 0


def test_stock_added_later_revalidates_the_saved_selection(client, plan):
    client.post("/cart/add/", {"plan": plan.pk})
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    response = client.get("/cart/")
    line = response.context["cart"].lines[0]
    assert line.purchasable
    assert response.context["cart"].total == plan.sale_price
    assert response.context["cart"].ready


def test_mutation_feedback_names_the_selection_and_the_actual_operation(client, plan):
    for route, word, quantity in [
        ("add", "اضافه شد", 1),
        ("quantity", "به‌روز شد", 2),
        ("remove", "حذف شد", 0),
    ]:
        data = client.post(
            "/cart/" + route + "/",
            {"plan": plan.pk, "quantity": quantity},
            HTTP_ACCEPT="application/json",
        ).json()
        assert plan.product.name in data["message"] and plan.title in data["message"]
        assert word in data["message"]
        assert not data.get("hold_message")
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    data = client.post("/cart/add/", {"plan": plan.pk}, HTTP_ACCEPT="application/json").json()
    assert "hold_message" not in data


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_unchanged_poll_does_not_reannounce_the_selected_option(browser, live_server, plan):
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.evaluate(
            "window.selectionChanges=0; new MutationObserver(xs=>window.selectionChanges+=xs.length).observe(document.querySelector('.buybox__selection'),{childList:true,subtree:true,characterData:true});"
        )
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(100)
        assert page.evaluate("window.selectionChanges") == 0
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_pending_add_keeps_focus_and_errors_stay_next_to_the_action(browser, live_server, plan):
    page = browser.new_page()
    held = []
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.route("**/cart/add/", lambda route: held.append(route))
        button = page.locator(".buybox__cta")
        button.click()
        expect(button).to_be_focused()
        button.evaluate("e=>{for(let n=0;n<10;n++) e.click()}")
        page.wait_for_timeout(100)
        assert len(held) == 1
        held[0].fulfill(
            status=400,
            content_type="application/json",
            body='{"message":"این محصول فعلاً موجود نیست."}',
        )
        expect(page.locator(".cart-add-error")).to_have_text("این محصول فعلاً موجود نیست.")
        page.clock.install()
        page.clock.fast_forward(7000)
        expect(page.locator(".cart-add-error")).to_be_visible()
        expect(page.locator("#cart-toast")).to_be_hidden()
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("state", ["unavailable", "empty-stock"])
def test_static_cart_states_do_not_reload_on_repeated_refresh(browser, live_server, plan, state):
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        if state == "unavailable":
            Plan.objects.filter(pk=plan.pk).update(is_available=False)
        else:
            Plan.objects.filter(pk=plan.pk).update(stock_quantity=0)
        page.goto(live_server.url + "/cart/")
        navigations = []
        page.on(
            "framenavigated",
            lambda frame: navigations.append(frame.url) if frame == page.main_frame else None,
        )
        for _ in range(3):
            with page.expect_response("**/cart/availability/**"):
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(100)
        page.wait_for_timeout(1200)
        assert not navigations
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_cart_refresh_keeps_an_edited_quantity_and_keyboard_focus(browser, live_server, plan):
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        page.goto(live_server.url + "/cart/")
        quantity = page.locator("input[name=quantity]")
        quantity.fill("2")
        Plan.objects.filter(pk=plan.pk).update(max_quantity=2)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(quantity).to_have_attribute("max", "2")
        expect(quantity).to_have_value("2")
        expect(quantity).to_be_focused()
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_newly_available_selection_gets_the_current_server_price_without_navigation(
    browser, live_server, plan
):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=0)
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        navigations = []
        page.on("framenavigated", lambda frame: navigations.append(frame.url))
        Plan.objects.filter(pk=plan.pk).update(stock_quantity=2, sale_price=330000)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator("[data-selected-price] .price__num")).to_have_text("۳۳۰,۰۰۰")
        expect(page.locator(".buybox__cta")).to_be_enabled()
        expect(page.locator("#qty")).to_have_attribute("max", "2")
        assert not navigations
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_stock_reduction_refreshes_in_place_without_losing_a_quantity_edit(
    browser, live_server, plan
):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        page.goto(live_server.url + "/cart/")
        quantity = page.locator("input[name=quantity]")
        quantity.fill("2")
        navigations = []
        page.on("framenavigated", lambda frame: navigations.append(frame.url))
        Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(quantity).to_have_attribute("max", "1")
        expect(quantity).to_have_value("2")
        expect(quantity).to_be_focused()
        assert not navigations
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_a_remaining_single_unit_does_not_hide_an_invalid_quantity(browser, live_server, plan):
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator("#qty").fill("2")
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        expect(page.locator("#qty")).to_be_visible()
        expect(page.locator("#qty")).to_have_attribute("max", "1")
        page.locator("#qty").fill("1")
        page.locator(".buybox__cta").click()
        expect(page.locator(".v-cart__count")).to_have_text("۳")
        expect(page.locator(".buybox__cart")).to_be_visible()
        assert CartItem.objects.get().quantity == 3
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("link", [".cart-line h2 a", ".cart-back", ".crumbs a"])
def test_cart_content_refresh_restores_focused_links(browser, live_server, plan, link):
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        page.goto(live_server.url + "/cart/")
        page.locator(link).focus()
        Plan.objects.filter(pk=plan.pk).update(max_quantity=2)
        with page.expect_response("**/cart/availability/**"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator("input[name=quantity]")).to_have_attribute("max", "2")
        expect(page.locator(link)).to_be_focused()
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_failed_add_reconciles_unavailable_quantity_controls_and_button(browser, live_server, plan):
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        button = page.locator(".buybox__cta")
        normal = button.evaluate("e=>getComputedStyle(e).backgroundColor")
        Plan.objects.filter(pk=plan.pk).update(stock_quantity=0)
        button.click()
        expect(page.locator(".cart-add-error")).to_be_visible()
        expect(button).to_be_disabled()
        expect(button).to_be_focused()
        expect(page.locator("#qty-row")).to_be_hidden()
        expect(page.locator("#qty")).to_be_disabled()
        expect(page.locator("#qty-limit")).to_be_hidden()
        assert button.evaluate("e=>getComputedStyle(e).backgroundColor") != normal
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_toast_keeps_a_latin_product_name_inside_a_right_to_left_line(browser, live_server, plan):
    Product.objects.filter(pk=plan.product_id).update(name="GitHub Copilot")
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        toast = page.locator("#cart-toast")
        expect(toast.locator("[data-toast-title]")).to_have_text("به سبد خرید اضافه شد")
        detail, name = toast.locator("[data-toast-detail]"), toast.locator("[data-toast-detail] bdi").first
        expect(name).to_have_text("GitHub Copilot")
        line, word = detail.bounding_box(), name.bounding_box()
        # Right to left, the first thing said sits at the right edge of the line.
        assert word["x"] + word["width"] >= line["x"] + line["width"] - 2
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.django_db(transaction=True)
def test_a_quantity_form_submitted_without_a_submitter_still_changes_the_quantity(
    browser, live_server, plan
):
    """What Safari before 15.4 sends: a submit event with no submitter."""
    page = browser.new_page()
    try:
        page.goto(live_server.url + plan.product.get_absolute_url())
        page.locator(".buybox__cta").click()
        expect(page.locator("#cart-toast")).to_be_visible()
        page.goto(live_server.url + "/cart/")
        page.locator(".cart-quantity input[name=quantity]").fill("2")
        with page.expect_navigation():
            page.locator(".cart-quantity").evaluate("form => form.requestSubmit()")
        expect(page.locator(".cart-quantity input[name=quantity]")).to_have_value("2")
        assert CartItem.objects.get().quantity == 2
    finally:
        page.close()

from decimal import Decimal

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.core.formatting import to_persian_digits
from apps.orders import services
from apps.orders.models import Order
from apps.orders.tests.test_customer_browser import capture, context_for
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_order_detail import CASES, order_for_state, second_plan

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize(
    "state,pill,badge,fragment,contact",
    CASES
    + [
        ("long", "در حال آماده‌سازی", "progress", "تحویل", False),
        ("unavailable", "در حال آماده‌سازی", "progress", "تحویل", False),
        ("error", "در حال آماده‌سازی", "progress", "تحویل", False),
    ],
)
def test_customer_order_states_keep_the_reading_order_and_keyboard_access(
    browser,
    live_server,
    buyer,
    plan,
    operator,
    settings,
    state,
    pill,
    badge,
    fragment,
    contact,
    width,
    tmp_path,
):
    if state == "long":
        plan.product.name = "محصول فارسی ChatGPT Plus با نام طولانی برای بررسی نمایش سفارش"
        plan.product.full_description = "توضیحات زمان خرید. " * 80
        plan.product.save()
    order = order_for_state(buyer, plan, operator, state)
    Order.objects.filter(pk=order.pk).update(
        policy_snapshot={"terms": {"title": "شرایط ثبت‌شده", "body": "متن زمان ثبت سفارش"}}
    )
    if state == "unavailable":
        settings.FIELD_ENCRYPTION_KEY = ""
    context = context_for(browser, live_server, buyer, width)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    detail = reverse("order-detail", args=[order.order_number])
    try:
        page.goto(live_server.url + detail)
        expect(page).to_have_url(live_server.url + detail)
        expect(
            page.get_by_role(
                "heading", name=f"سفارش {to_persian_digits(order.order_number)}", exact=True
            )
        ).to_be_visible()
        expect(page.locator("#order-next-title")).to_contain_text(fragment)
        expect(page.locator(f"#order-next .badge--{badge}")).to_have_text(pill)
        boxes = [
            page.locator(selector).bounding_box()
            for selector in ("#order-next", "#order-purchase", ".order-records")
        ]
        assert all(abs(box["x"] - boxes[0]["x"]) < 1 for box in boxes)
        assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]
        assert page.locator("[data-support-contact]").count() == int(contact)
        if state in ("error", "unavailable"):  # support waits closed among the records
            expect(page.locator("#order-help")).not_to_have_attribute("open", "")
            page.locator("#order-help > summary").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#order-help")).to_have_attribute("open", "")
        if state == "error":
            page.locator(".order-support-replace > summary").focus()
            page.keyboard.press("Enter")
            page.locator('[name="expected_support_token"]').evaluate("el => {el.value = 'stale';}")
            page.get_by_role("button", name="ساخت شناسه‌ی جدید", exact=True).click()
            expect(page.get_by_role("alert")).to_have_text(
                "شناسه تغییر کرده است؛ صفحه را دوباره باز کنید."
            )
            expect(page).to_have_url(
                live_server.url + reverse("order-support-replace", args=[order.order_number])
            )
        if state == "unavailable":
            expect(page.get_by_text("شناسه‌ی پشتیبانی در دسترس نیست.", exact=True)).to_be_visible()
            expect(
                page.locator("[data-copy-support], [name=expected_support_token]")
            ).to_have_count(0)
            expect(
                page.locator("#order-help").get_by_role(
                    "link", name="@premshop_support", exact=True
                )
            ).to_be_visible()
        capture(page, f"order-{state}", width, tmp_path)
        if contact:  # what to send sits in the card, beside the button that opens the chat
            if state == "delivered":  # inside the warranty it waits behind one quiet question
                expect(page.locator("#order-next #support-identifier")).to_be_hidden()
                page.locator(".order-contact-more > summary").focus()
                page.keyboard.press("Enter")
            expect(page.locator("#order-next #support-identifier")).to_be_visible()
            expect(
                page.locator("#order-next").get_by_role("link", name="باز کردن تلگرام پشتیبانی")
            ).to_have_attribute("href", "https://t.me/premshop_support")
        page.locator("#order-conditions > summary").focus()
        page.keyboard.press("Enter")
        if state == "long":
            expect(page.get_by_text("توضیحات محصول", exact=True)).to_be_visible()
        page.locator(".saved-policy > summary").focus()
        page.keyboard.press("Enter")
        expect(page.get_by_text("متن زمان ثبت سفارش", exact=True)).to_be_visible()
        page.locator("#order-tracking > summary").focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("link", name="لینک پیگیری سفارش", exact=True)).to_be_visible()
        if state != "empty":
            page.locator("#order-history > summary").focus()
            page.keyboard.press("Enter")
            expect(page.locator(".order-timeline")).to_be_visible()
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_a_discounted_order_in_two_categories_shows_its_subtotal_and_names(
    browser, live_server, buyer, plan, width, tmp_path
):
    order = services.place_order(
        user=buyer, lines=[services.OrderLine(plan, 1), services.OrderLine(second_plan(), 1)]
    )
    Order.objects.filter(pk=order.pk).update(
        discount_amount=Decimal("50000"),
        total_amount=order.total_amount - Decimal("50000"),
        discount_snapshot={"code": "SAVE"},
    )
    context = context_for(browser, live_server, buyer, width)
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("order-detail", args=[order.order_number]))
        expect(page.locator(".order-category__name")).to_have_text(["نمونه", "دسته‌ی دوم"])
        expect(page.locator(".order-totals dt")).to_have_text(
            ["جمع محصولات", "تخفیف SAVE", "مبلغ نهایی سفارش"]
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        capture(page, "order-discounted", width, tmp_path)
    finally:
        context.close()

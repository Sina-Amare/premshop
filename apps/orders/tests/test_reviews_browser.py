import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.orders.tests.test_customer_browser import capture, context_for
from apps.orders.tests.test_delivery import deliver, queued
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_reviews import reviews

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_review_states_and_keyboard(browser, live_server, buyer, plan, operator, width, tmp_path):
    order, item = queued(buyer, plan, operator)
    order_url = live_server.url + reverse("order-detail", args=[order.order_number])
    product_url = live_server.url + plan.product.get_absolute_url()
    with context_for(browser, live_server, buyer, width) as context:
        page = context.new_page()
        page.goto(order_url)
        expect(page.locator("#order-next-title")).to_be_visible()
        expect(page.locator("#order-reviews")).to_have_count(0)
        expect(page).to_have_url(order_url)
        capture(page, "order-ineligible", width, tmp_path)

        page.goto(product_url)
        expect(page.get_by_role("heading", name="نظر خریداران")).to_be_visible()
        expect(page.get_by_text("هنوز نظری منتشر نشده است.")).to_be_visible()
        expect(page).to_have_url(product_url)
        capture(page, "product-empty", width, tmp_path)

        deliver(item, operator)
        page.goto(order_url)
        section = page.locator("#order-reviews")
        stars = section.get_by_role("group", name="امتیاز")
        four = section.get_by_label("۴ از ۵")
        text = section.get_by_label("نظر (اختیاری)")
        submit = section.get_by_role("button", name="ثبت نظر")
        expect(page).to_have_url(order_url)
        expect(section).to_have_css("direction", "rtl")
        expect(stars.get_by_role("radio")).to_have_count(5)
        capture(page, "order-ready", width, tmp_path)
        section.get_by_label("۱ از ۵").focus()
        for _ in range(3):
            page.keyboard.press("ArrowDown")
        expect(four).to_be_checked()
        page.keyboard.press("Tab")
        expect(text).to_be_focused()
        text.fill("buyer@example.test")
        page.keyboard.press("Tab")
        expect(submit).to_be_focused()
        page.keyboard.press("Enter")
        expect(section.get_by_role("alert")).to_contain_text("ایمیل یا شماره‌ی موبایل")
        expect(page).to_have_url(order_url + f"items/{item.pk}/review/#order-reviews")
        expect(text).to_have_value("buyer@example.test")
        expect(four).to_be_checked()
        capture(page, "order-error", width, tmp_path)

        section.locator(f"label[for='review-{item.pk}-4']").click()
        expect(four).to_be_checked()
        text.fill("بسیار خوب‌ بود. " + "MixedContent" * 75)
        submit.click()
        expect(page).to_have_url(order_url + "#order-reviews")
        expect(section.get_by_text("در انتظار بررسی")).to_be_visible()
        expect(section.get_by_role("img", name="۴ از ۵")).to_be_visible()
        expect(
            section.get_by_text("اگر نظرتان تأیید شود، در صفحه‌ی محصول دیده می‌شود.")
        ).to_be_visible()
        capture(page, "order-pending", width, tmp_path)

        review = reviews().objects.get(order_item=item)
        review.status = "published"
        review.save(update_fields=["status"])
        page.goto(product_url)
        expect(page).to_have_url(product_url)
        expect(page.get_by_role("heading", name="نظر خریداران")).to_be_visible()
        expect(page.get_by_role("link", name="۴ از ۵ · ۱ نظر")).to_have_attribute(
            "href", "#product-reviews"
        )
        expect(page.locator("[data-review-summary]")).to_contain_text("۱ نظر")
        expect(page.get_by_text("بسیار خوب‌ بود.", exact=False)).to_have_css("direction", "rtl")
        capture(page, "product-published-long", width, tmp_path)

        review.status = "hidden"
        review.save(update_fields=["status"])
        page.goto(order_url)
        expect(page).to_have_url(order_url)
        expect(section.get_by_text("این نظر در صفحه‌ی محصول منتشر نمی‌شود.")).to_be_visible()
        expect(section.get_by_text("پنهان‌شده")).to_be_visible()
        expect(section.get_by_text("MixedContent", exact=False)).to_have_count(0)
        capture(page, "order-hidden", width, tmp_path)

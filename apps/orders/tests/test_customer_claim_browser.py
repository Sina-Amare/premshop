from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.core.tests.test_accessibility_browser import AXE, RUN_AXE, SERIOUS, STYLED
from apps.orders.models import Delivery
from apps.orders.tests.test_customer_browser import capture, context_for
from apps.orders.tests.test_customer_claim import LABEL_A, A, B, C, delivered, submit
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_replacement import reject, replace

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize(
    "state",
    [
        "empty",
        "open",
        "rejected",
        "replaced",
        "expired",
        "invalid",
        "stale",
        "long",
        "no_end",
        "warranty",
    ],
)
def test_customer_claim_states(browser, live_server, buyer, plan, operator, width, state, tmp_path):
    if state == "long":
        plan.product.name = (
            "اشتراک نمونه با نام بلند Example Pro برای خواندن اطلاعات سفارش و درخواست تعویض"
        )
        plan.product.save()
    order, item = delivered(
        buyer,
        plan,
        operator,
        warranty="none" if state == "expired" else "days_7",
        days=None if state == "no_end" else 30,
    )
    if state in ("open", "rejected", "replaced"):
        submit(item, buyer)
        if state == "rejected":
            reject(item, operator)
        elif state == "replaced":
            replace(item, operator, expires_at=timezone.localdate() + timedelta(days=30))
    if state in ("expired", "warranty"):
        Delivery.objects.filter(order_item=item).update(
            created_at=timezone.now() - timedelta(hours=49)
        )
    context = context_for(browser, live_server, buyer, width)
    page = context.new_page()
    try:
        target = live_server.url + reverse("order-detail", args=[order.order_number])
        page.goto(target)
        expect(page).to_have_url(target)
        expect(page.locator("#order-delivery-title")).to_have_text("اطلاعات تحویل")
        box = page.locator(f'[data-claim-item="{item.pk}"]')
        expect(box).to_be_visible()
        if state == "open":
            expect(box.locator('[data-state="claim_open"]')).to_be_visible()
            assert box.locator("form").count() == 0
        elif state == "expired":
            expect(box).to_contain_text("مهلت درخواست تعویض گذشته است.")
        else:
            box.locator("summary").click()
            if state in ("rejected", "replaced"):
                expect(box.locator(f'[data-state="claim_{state}"]')).to_be_visible()
            if state == "no_end":
                assert box.locator('input[name="reason"]').count() == 1
                expect(box.locator(f'input[value="{A}"]')).to_be_visible()
            elif state == "warranty":
                assert box.locator('input[name="reason"]').count() == 1
                expect(box.locator(f'input[value="{C}"]')).to_be_visible()
            else:
                expect(box.get_by_label(LABEL_A, exact=True)).to_be_visible()
                expect(box.locator(f'input[value="{B}"]')).to_be_visible()
            if state == "invalid":
                box.locator(f'input[value="{A}"]').evaluate(
                    "node => { node.checked = true; node.value = 'invalid'; }"
                )
                box.get_by_role("button", name="ثبت درخواست تعویض", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("یکی از دلیل‌ها را انتخاب کنید.")
            elif state == "stale":
                submit(item, buyer)
                reject(item, operator)
                box.get_by_label(LABEL_A, exact=True).check()
                box.get_by_role("button", name="ثبت درخواست تعویض", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("وضعیت محصول تغییر کرده است")
        assert page.locator("html").get_attribute("dir") == "rtl"
        page.add_script_tag(path=str(AXE))
        violations = page.evaluate(RUN_AXE, STYLED)["violations"]
        assert not [issue for issue in violations if issue["impact"] in SERIOUS]
        capture(page, f"claim-{state}", width, tmp_path)
        if state == "empty":
            radio = box.get_by_label(LABEL_A, exact=True)
            radio.focus()
            page.keyboard.press("Space")
            page.keyboard.press("Tab")
            expect(box.get_by_role("button", name="ثبت درخواست تعویض", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            expect(page.locator('[data-state="claim_open"]')).to_be_visible()
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_panel_shows_claim_reason_and_customer_source(
    browser, live_server, buyer, plan, operator, width, tmp_path
):
    order, item = delivered(buyer, plan, operator)
    submit(item, buyer)
    context = context_for(browser, live_server, operator, width)
    page = context.new_page()
    try:
        target = live_server.url + reverse("admin:orders_order_deliver", args=[order.pk, item.pk])
        page.goto(target)
        expect(page).to_have_url(target)
        expect(page.locator(".delivery-claim")).to_have_text("دلیل درخواست تعویض: " + LABEL_A)
        expect(page.locator(".delivery-claim + p")).to_have_text("ثبت‌کننده: مشتری")
        capture(page, "claim-panel-open", width, tmp_path)
        target = live_server.url + reverse("admin:orders_order_change", args=[order.pk])
        page.goto(target)
        expect(page).to_have_url(target)
        expect(page.locator(".order-timeline__note").filter(has_text=LABEL_A)).to_be_visible()
        capture(page, "claim-panel-history", width, tmp_path)
    finally:
        context.close()

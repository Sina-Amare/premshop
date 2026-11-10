import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.telegram import mask_email
from apps.core.formatting import to_persian_digits
from apps.orders import services as orders
from apps.orders import telegram_panel
from apps.orders.models import OrderItem
from apps.orders.selectors import customer_summary
from apps.orders.tests.test_input_clock import operator as operator
from apps.payments.services import record_manual_payment

pytestmark = pytest.mark.django_db


def placed(user, plan):
    return orders.place_order(user=user, lines=[orders.OrderLine(plan, 1)])


def paid(order, operator):
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref=f"REF-{order.pk}"
    )
    return order


def targets(buttons):
    return [b.get("callback_data") or b.get("url") for row in buttons for b in row]


def links_in(text, buttons):
    hrefs = re.findall(r'href="([^"]+)"', text)
    return hrefs + [b["url"] for row in buttons for b in row if "url" in b]


def test_home_names_the_account_and_counts_its_open_orders(buyer, plan, operator, settings):
    paid(placed(buyer, plan), operator)
    placed(buyer, plan)
    text, buttons = telegram_panel.render(buyer, "home")
    assert mask_email(buyer.email) in text
    assert "سفارش‌های در جریان: ۲" in text
    assert settings.SITE_URL + reverse("account") in text
    assert targets(buttons) == ["p:orders", "https://t.me/premshop_support"]


def test_the_orders_screen_lists_the_customers_own_recent_orders(buyer, plan, operator):
    stranger = placed(User.objects.create_user("other@example.test"), plan)
    # One unpaid order at a time: the earlier ones are paid, the newest waits.
    mine = [paid(placed(buyer, plan), operator) for _ in range(telegram_panel.RECENT)]
    mine.append(placed(buyer, plan))
    text, buttons = telegram_panel.render(buyer, "orders")
    shown = [target for target in targets(buttons) if target.startswith("p:o:")]
    assert shown == [f"p:o:{order.order_number}" for order in reversed(mine[1:])]
    assert f"سفارش {to_persian_digits(stranger.order_number)}" not in text
    assert "در صف انجام" in text and "منتظر پرداخت" in text
    assert targets(buttons)[-1] == "p:home"


def test_an_order_screen_shows_its_facts_and_its_page(buyer, plan, settings):
    order = placed(buyer, plan)
    text, buttons = telegram_panel.render(buyer, f"o:{order.order_number}")
    assert f"<b>سفارش {to_persian_digits(order.order_number)}</b>" in text
    assert "منتظر پرداخت" in text and "مهلت پرداخت: " in text
    assert "سرویس نمونه، یک ماهه" in text
    assert "مبلغ: ۲۰۰,۰۰۰ تومان" in text
    assert settings.SITE_URL + reverse("order-detail", args=[order.order_number]) in text
    assert buttons == [
        [{"text": "کپی شماره‌ی سفارش", "copy_text": {"text": str(order.order_number)}}],
        [{"text": "بازگشت به سفارش‌ها", "callback_data": "p:orders"}],
    ]


NOTHING_TO_DO = "لازم نیست کاری انجام دهید."


@pytest.mark.parametrize(
    ("state", "sentence"),
    [
        ("AWAITING_INPUT", "اطلاعات فعال‌سازی را در تلگرام برای پشتیبانی بفرستید."),
        ("REPLACEMENT_REQUESTED", NOTHING_TO_DO),
        ("QUEUED", NOTHING_TO_DO),
        ("DELIVERED", NOTHING_TO_DO),
        ("CANCELLED", "برای پیگیری پرداخت یا بازپرداخت، با پشتیبانی تماس بگیرید."),
        ("REFUNDED", NOTHING_TO_DO),
        ("", "برای پیگیری سفارش با پشتیبانی تماس بگیرید."),
    ],
)
def test_every_state_names_its_next_step_as_the_order_page_does(buyer, plan, state, sentence):
    summary = {**customer_summary(placed(buyer, plan)), "next_step": state}
    text = render_to_string("telegram/panel_order.html", {**summary, "order_url": "/"})
    assert sentence in text


@pytest.mark.parametrize("screen", ["o:{other}", "o:x", "o:", "o:-1", "o:" + "9" * 30, "o:²", "nonsense"])
def test_a_screen_naming_no_order_of_the_customers_shows_the_home(buyer, plan, screen):
    other = placed(User.objects.create_user("other@example.test"), plan)
    text, _ = telegram_panel.render(buyer, screen.format(other=other.order_number))
    assert text == telegram_panel.render(buyer, "home")[0]


def test_no_screen_carries_input_or_a_way_to_pay(buyer, plan, settings):
    order = placed(buyer, plan)
    OrderItem.objects.filter(order=order).update(customer_input="SECRET-INPUT")
    allowed = {
        settings.SITE_URL + reverse("account"),
        settings.SITE_URL + reverse("order-list"),
        settings.SITE_URL + reverse("order-detail", args=[order.order_number]),
        "https://t.me/premshop_support",
    }
    for screen in ["home", "orders", f"o:{order.order_number}"]:
        text, buttons = telegram_panel.render(buyer, screen)
        assert "SECRET-INPUT" not in text
        # Telegram requires its Stars for goods sold inside a bot: the bot only points to pages.
        assert set(links_in(text, buttons)) <= allowed


def test_catalogue_text_is_escaped_in_every_screen(buyer, plan):
    plan.product.name = "<b>&"
    plan.product.save(update_fields=["name"])
    order = placed(buyer, plan)
    for screen in ["orders", f"o:{order.order_number}"]:
        text, _ = telegram_panel.render(buyer, screen)
        assert "&lt;b&gt;&amp;" in text and "<b>&" not in text


def test_no_panel_line_opens_with_a_latin_letter(buyer, plan):
    # Telegram sets each line's direction from its first letter: a line opening with «Poe»
    # turns left to right and scrambles its Persian (design-language, Voice).
    plan.product.name = "Poe"
    plan.product.save(update_fields=["name"])
    order = placed(buyer, plan)
    for screen in ["home", "orders", f"o:{order.order_number}"]:
        text, _ = telegram_panel.render(buyer, screen)
        for line in re.sub(r"<[^>]+>", "", text).splitlines():
            letters = [char for char in line if char.isalpha()]
            assert not letters or not letters[0].isascii(), line


def test_a_customer_with_no_orders_is_told_so(buyer):
    text, buttons = telegram_panel.render(buyer, "orders")
    assert "هنوز سفارشی ثبت نکرده‌اید." in text and targets(buttons) == ["p:home"]

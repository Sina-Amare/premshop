import json
import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.telegram import mask_email
from apps.core.formatting import to_persian_digits
from apps.orders import services as orders
from apps.orders import status as order_status
from apps.orders import telegram_emoji as emoji
from apps.orders import telegram_panel
from apps.orders.models import OrderItem
from apps.orders.selectors import customer_summary
from apps.orders.tests.test_delivery import NOTE, VALUE, deliver, queued
from apps.orders.tests.test_input_clock import operator as operator
from apps.payments.services import record_manual_payment

pytestmark = pytest.mark.django_db

S = OrderItem.Status
LISTS = ["l:unpaid", "l:active", "l:delivered", "l:closed"]


def placed(user, plan):
    return orders.place_order(user=user, lines=[orders.OrderLine(plan, 1)])


def paid(order, operator):
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref=f"REF-{order.pk}"
    )
    return order


def targets(buttons):
    return [b.get("callback_data") or b.get("url") for row in buttons for b in row]


def shown_orders(buttons):
    return [t.removeprefix("p:o:") for t in targets(buttons) if t.startswith("p:o:")]


def links_in(text, buttons):
    hrefs = re.findall(r'href="([^"]+)"', text)
    return hrefs + [b["url"] for row in buttons for b in row if "url" in b]


def test_home_is_a_grid_counting_each_kind_of_order(buyer, plan, operator, settings):
    paid(placed(buyer, plan), operator)
    placed(buyer, plan)
    placed(User.objects.create_user("stranger@example.test"), plan)
    text, buttons = telegram_panel.render(buyer, "home")
    assert mask_email(buyer.email) in text
    site = settings.SITE_URL
    assert buttons == [
        [
            {"text": f"{emoji.UNPAID} منتظر پرداخت ۱", "callback_data": "p:l:unpaid"},
            {"text": f"{emoji.ACTIVE} در جریان ۱", "callback_data": "p:l:active"},
        ],
        [
            {"text": f"{emoji.DELIVERED} تحویل‌شده ۰", "callback_data": "p:l:delivered"},
            {"text": f"{emoji.CLOSED} لغو و بازپرداخت ۰", "callback_data": "p:l:closed"},
        ],
        [{"text": f"{emoji.BUY} خرید از سایت", "url": site + reverse("home")}],
        [
            {"text": f"{emoji.ACCOUNT} حساب کاربری", "url": site + reverse("account")},
            {"text": f"{emoji.SUPPORT} پشتیبانی", "url": "https://t.me/premshop_support"},
        ],
    ]


@pytest.mark.parametrize(
    ("statuses", "home"),
    [
        ((S.PENDING_PAYMENT,), "l:unpaid"),
        ((S.QUEUED,), "l:active"),
        ((S.AWAITING_INPUT,), "l:active"),
        ((S.REPLACEMENT_REQUESTED,), "l:active"),
        ((S.DELIVERED,), "l:delivered"),
        ((S.CANCELLED,), "l:closed"),
        ((S.REFUNDED,), "l:closed"),
        ((S.DELIVERED, S.QUEUED), "l:active"),
        ((S.DELIVERED, S.REFUNDED), "l:delivered"),
        ((S.CANCELLED, S.REFUNDED), "l:closed"),
    ],
)
def test_an_orders_item_statuses_choose_its_one_list(statuses, home):
    assert "l:" + telegram_panel.list_for(set(statuses)) == home


def test_every_order_shows_in_its_list_alone_and_is_counted_there(buyer, plan, operator):
    delivered, item = queued(buyer, plan, operator)
    deliver(item, operator, note=NOTE)
    active = paid(placed(buyer, plan), operator)
    closed = placed(buyer, plan)
    orders.cancel_unpaid_order(order_id=closed.pk, actor=buyer)
    unpaid = placed(buyer, plan)
    expected = {
        "l:delivered": [str(delivered.order_number)],
        "l:active": [str(active.order_number)],
        "l:closed": [str(closed.order_number)],
        "l:unpaid": [str(unpaid.order_number)],
    }
    assert {screen: shown_orders(telegram_panel.render(buyer, screen)[1]) for screen in LISTS} == (
        expected
    )
    grid = telegram_panel.render(buyer, "home")[1][:2]
    assert [b["text"][-1] for row in grid for b in row] == ["۱", "۱", "۱", "۱"]


def test_a_list_shows_the_customers_own_recent_orders(buyer, plan, operator):
    stranger = paid(placed(User.objects.create_user("other@example.test"), plan), operator)
    mine = [paid(placed(buyer, plan), operator) for _ in range(telegram_panel.RECENT + 1)]
    text, buttons = telegram_panel.render(buyer, "l:active")
    expected = [str(order.order_number) for order in reversed(mine[1:])]
    assert shown_orders(buttons) == expected
    assert f"سفارش {to_persian_digits(stranger.order_number)}" not in text
    assert "در حال آماده‌سازی" in text
    assert buttons[-1] == [{"text": f"{emoji.BACK} بازگشت", "callback_data": "p:home"}]


def test_each_order_carries_its_products_emoji(buyer, plan):
    plan.product.name = "ChatGPT Plus"
    plan.product.save(update_fields=["name"])
    order = placed(buyer, plan)
    text, buttons = telegram_panel.render(buyer, "l:unpaid")
    number = to_persian_digits(order.order_number)
    assert buttons[0][0]["text"] == f"{emoji.PRODUCTS['chatgpt']} سفارش {number}"
    assert f"{emoji.PRODUCTS['chatgpt']} <b>سفارش {number}</b>" in text
    text, _ = telegram_panel.render(buyer, f"o:{order.order_number}")
    assert f"{emoji.PRODUCTS['chatgpt']} <b>سفارش {number}</b>" in text


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("ChatGPT Plus", "chatgpt"),
        ("Apple Music", "apple-music"),
        ("Apple Gift Card", "apple"),
        ("Google Play Gift Card", "google-play"),
        ("Google AI Pro", "google-ai"),
        ("Disney+", "disney"),
        ("GitHub Copilot", "github"),
    ],
)
def test_a_product_takes_the_emoji_of_its_narrowest_name(name, key):
    assert emoji.for_product(name) == emoji.PRODUCTS[key]


@pytest.mark.parametrize("name", ["Poetry Box", "سرویس نمونه", ""])
def test_an_unknown_product_takes_the_plain_box(name):
    # «Poetry» holds «poe», which must not make it the Poe chat app.
    assert emoji.for_product(name) == emoji.OTHER_PRODUCT


def test_an_order_screen_shows_its_facts_and_returns_to_its_list(buyer, plan, settings):
    order = placed(buyer, plan)
    text, buttons = telegram_panel.render(buyer, f"o:{order.order_number}")
    assert f"<b>سفارش {to_persian_digits(order.order_number)}</b>" in text
    assert "در انتظار پرداخت" in text and "مهلت پرداخت: " in text
    assert "سرویس نمونه، یک ماهه" in text
    assert "مبلغ: ۲۰۰,۰۰۰ تومان" in text
    assert settings.SITE_URL + reverse("order-detail", args=[order.order_number]) in text
    assert buttons == [
        [
            {
                "text": f"{emoji.COPY} کپی شماره‌ی سفارش",
                "copy_text": {"text": str(order.order_number)},
            }
        ],
        [{"text": f"{emoji.BACK} بازگشت", "callback_data": "p:l:unpaid"}],
    ]


NOTHING_TO_DO = "لازم نیست کاری انجام دهید."


@pytest.mark.parametrize(
    ("state", "sentence"),
    [
        ("awaiting_input", "اطلاعات فعال‌سازی را در تلگرام برای پشتیبانی بفرستید."),
        ("refund_owed", "شماره‌ی کارت یا شبای به نام خودتان را برای پشتیبانی بفرستید."),
        ("late", "زمان تحویل گذشته است. برای پیگیری، به پشتیبانی پیام بدهید."),
        ("replacement", NOTHING_TO_DO),
        ("queued", NOTHING_TO_DO),
        ("delivered", NOTHING_TO_DO),
        ("refunded", NOTHING_TO_DO),
        ("expired", "پرداختی برای این سفارش ثبت نشد."),
        ("cancelled", "پرداختی برای این سفارش ثبت نشده بود."),
    ],
)
def test_every_state_names_its_next_step_as_the_order_page_does(buyer, plan, state, sentence):
    summary = customer_summary(placed(buyer, plan))
    summary["card"] = {**summary["card"], **order_status.pill(state)}
    text = render_to_string("telegram/panel_order.html", {**summary, "order_url": "/"})
    assert sentence in text and "تماس بگیرید" not in text


@pytest.mark.parametrize(
    "screen",
    ["o:{other}", "o:x", "o:", "o:-1", "o:" + "9" * 30, "o:²", "l:", "l:all", "orders", "nonsense"],
)
def test_a_screen_naming_no_order_or_list_of_the_customers_shows_the_home(buyer, plan, screen):
    other = placed(User.objects.create_user("other@example.test"), plan)
    text, _ = telegram_panel.render(buyer, screen.format(other=other.order_number))
    assert text == telegram_panel.render(buyer, "home")[0]


def screens(order):
    return ["home", *LISTS, f"o:{order.order_number}"]


def test_no_screen_carries_input_or_a_way_to_pay(buyer, plan, settings):
    order = placed(buyer, plan)
    OrderItem.objects.filter(order=order).update(customer_input="SECRET-INPUT")
    allowed = {
        settings.SITE_URL + reverse("home"),
        settings.SITE_URL + reverse("account"),
        settings.SITE_URL + reverse("order-list"),
        settings.SITE_URL + reverse("order-detail", args=[order.order_number]),
        "https://t.me/premshop_support",
    }
    for screen in screens(order):
        text, buttons = telegram_panel.render(buyer, screen)
        assert "SECRET-INPUT" not in text
        # Telegram requires its Stars for goods sold inside a bot: the bot only points to pages.
        assert set(links_in(text, buttons)) <= allowed


def test_a_delivered_credential_stays_off_every_screen(buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    deliver(item, operator, note=NOTE)
    for screen in screens(order):
        text, buttons = telegram_panel.render(buyer, screen)
        shown = text + json.dumps(buttons, ensure_ascii=False)
        assert VALUE not in shown and "note-secret-K9" not in shown


def test_catalogue_text_is_escaped_in_every_screen(buyer, plan):
    plan.product.name = "<b>&"
    plan.product.save(update_fields=["name"])
    order = placed(buyer, plan)
    for screen in ["l:unpaid", f"o:{order.order_number}"]:
        text, _ = telegram_panel.render(buyer, screen)
        assert "&lt;b&gt;&amp;" in text and "<b>&" not in text


def test_no_panel_line_or_button_opens_with_a_latin_letter(buyer, plan):
    # Telegram sets each line's direction from its first letter: a line opening with «Poe»
    # turns left to right and scrambles its Persian (design-language, Voice).
    plan.product.name = "Poe"
    plan.product.save(update_fields=["name"])
    order = placed(buyer, plan)
    for screen in screens(order):
        text, buttons = telegram_panel.render(buyer, screen)
        lines = re.sub(r"<[^>]+>", "", text).splitlines()
        for line in lines + [b["text"] for row in buttons for b in row]:
            letters = [char for char in line if char.isalpha()]
            assert not letters or not letters[0].isascii(), line


def test_an_empty_list_says_so(buyer):
    text, buttons = telegram_panel.render(buyer, "l:active")
    assert "سفارشی در این بخش ندارید." in text and targets(buttons) == ["p:home"]

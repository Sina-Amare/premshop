import json
import re
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.conf import settings as django_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.telegram import account_label
from apps.accounts.tests.test_telegram_link import framed
from apps.core import support
from apps.core import telegram_emoji as emoji
from apps.core.formatting import to_persian_digits
from apps.core.templatetags.fa import jalali
from apps.orders import services as orders
from apps.orders import status as order_status
from apps.orders import telegram_panel
from apps.orders.models import Order, OrderItem
from apps.orders.selectors import customer_orders, customer_summary, order_lead
from apps.orders.tests.test_delivery import NOTE, VALUE, deliver, queued
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_paid_cancellation import cancel
from apps.payments.models import Refund
from apps.payments.services import record_manual_payment
from apps.payments.tests.test_refund_execution import execute

pytestmark = pytest.mark.django_db

S = OrderItem.Status
LISTS = ["l:unpaid", "l:current", "l:delivered", "l:cancelled"]
SUPPORT = {
    "text": f"{emoji.SUPPORT} پیام به پشتیبانی",
    "url": support.details()["telegram_url"],
    "style": "primary",
}
BACK_HOME = [{"text": f"{emoji.BACK} بازگشت", "callback_data": "p:home"}]


def share_url(site):
    return "https://t.me/share/url?" + urlencode({"url": site + reverse("home")})


def all_orders(site):
    return [{"text": f"{emoji.WEB} همه‌ی سفارش‌ها در سایت", "url": site + reverse("order-list")}]


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
    assert f"{emoji.ACCOUNT} حساب کاربری: {account_label(buyer)}" in text
    framed(text, buttons)
    site = settings.SITE_URL
    assert buttons == [
        [
            {"text": f"{emoji.UNPAID} در انتظار پرداخت ۱", "callback_data": "p:l:unpaid"},
            {"text": f"{emoji.ACTIVE} در جریان ۱", "callback_data": "p:l:current"},
        ],
        [
            {"text": f"{emoji.DELIVERED} تحویل‌شده ۰", "callback_data": "p:l:delivered"},
            {"text": f"{emoji.CLOSED} لغوشده ۰", "callback_data": "p:l:cancelled"},
        ],
        # Buying is the home's one main action, so it alone is coloured.
        [{"text": f"{emoji.BUY} خرید از سایت", "url": site + reverse("home"), "style": "primary"}],
        [
            {"text": f"{emoji.ACCOUNT} حساب کاربری", "url": site + reverse("account")},
            {"text": f"{emoji.SUPPORT} پشتیبانی", "url": "https://t.me/premshop_support"},
        ],
        [{"text": f"{emoji.SHARE} معرفی PremShop", "url": share_url(site)}],
    ]


def test_the_bot_files_every_order_where_the_sites_order_list_does(buyer, plan, operator):
    """The three orders the bot once filed by raw status: a lapsed window not yet swept, a refund
    still owed, and a delivered product beside one. The site files each by its lead state."""
    plan.input_type, plan.telegram_required, plan.max_quantity = "none", False, 2
    plan.save()
    # An open unpaid order blocks the next one, so the paid orders come first.
    queue = paid(placed(buyer, plan), operator)
    owed = paid(placed(buyer, plan), operator)
    cancel(owed.items.get(), operator)
    mixed = paid(orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 2)]), operator)
    first, second = mixed.items.order_by("pk")
    deliver(first, operator, note=NOTE)
    cancel(second, operator)
    delivered = paid(placed(buyer, plan), operator)
    deliver(delivered.items.get(), operator, note=NOTE)
    refunded = paid(placed(buyer, plan), operator)
    cancel(refunded.items.get(), operator)
    execute(refunded, list(Refund.objects.filter(order_item__order=refunded)), operator)
    withdrawn = placed(buyer, plan)
    orders.cancel_unpaid_order(order_id=withdrawn.pk, actor=buyer)
    # Last: placing another order would sweep it, and the open unpaid group has its own test.
    lapsed = placed(buyer, plan)
    Order.objects.filter(pk=lapsed.pk).update(
        unpaid_expires_at=timezone.now() - timedelta(minutes=1)
    )
    assert lapsed.items.get().status == S.PENDING_PAYMENT, "positive control: not yet swept"
    expected: dict[str, set[Order]] = {
        "unpaid": set(),
        "current": {queue, owed, mixed},
        "delivered": {delivered},
        "cancelled": {lapsed, withdrawn, refunded},
    }
    now = timezone.now()
    for key, members in expected.items():
        numbers = {str(order.order_number) for order in members}
        site = {
            str(order.order_number)
            for order in customer_orders(buyer)
            if order_status.tab_of(order_lead(order, now)) == key
        }
        shown = set(shown_orders(telegram_panel.render(buyer, f"l:{key}")[1]))
        assert shown == site == numbers, key
        text = telegram_panel.render(buyer, f"l:{key}")[0]
        assert order_status.TABS[key][0] in text


def test_every_order_shows_in_its_list_alone_and_is_counted_there(buyer, plan, operator):
    delivered, item = queued(buyer, plan, operator)
    deliver(item, operator, note=NOTE)
    active = paid(placed(buyer, plan), operator)
    closed = placed(buyer, plan)
    orders.cancel_unpaid_order(order_id=closed.pk, actor=buyer)
    unpaid = placed(buyer, plan)
    expected = {
        "l:delivered": [str(delivered.order_number)],
        "l:current": [str(active.order_number)],
        "l:cancelled": [str(closed.order_number)],
        "l:unpaid": [str(unpaid.order_number)],
    }
    assert {screen: shown_orders(telegram_panel.render(buyer, screen)[1]) for screen in LISTS} == (
        expected
    )
    grid = telegram_panel.render(buyer, "home")[1][:2]
    assert [b["text"][-1] for row in grid for b in row] == ["۱", "۱", "۱", "۱"]


def test_a_list_shows_the_customers_own_recent_orders(buyer, plan, operator, settings):
    stranger = paid(placed(User.objects.create_user("other@example.test"), plan), operator)
    mine = [paid(placed(buyer, plan), operator) for _ in range(telegram_panel.RECENT + 1)]
    text, buttons = telegram_panel.render(buyer, "l:current")
    framed(text, buttons)
    expected = [str(order.order_number) for order in reversed(mine[1:])]
    assert shown_orders(buttons) == expected
    assert f"سفارش {to_persian_digits(stranger.order_number)}" not in text
    assert buttons[-2:] == [all_orders(settings.SITE_URL), BACK_HOME]


def test_a_list_card_labels_its_product_status_and_date(buyer, plan, operator):
    order = paid(placed(buyer, plan), operator)
    text, _ = telegram_panel.render(buyer, "l:current")
    card = re.search(r"<blockquote>(.+?)</blockquote>", text, re.S).group(1).split("\n")
    assert card[1:] == [
        f"{emoji.PRODUCT} محصول: سرویس نمونه",
        f"{emoji.STATUS} وضعیت: در حال آماده‌سازی",
        f"{emoji.DATE} {jalali(order.created_at)}",
    ]


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
    framed(text, buttons)
    assert f"<b>سفارش {to_persian_digits(order.order_number)}</b>" in text
    state = re.search(r"<blockquote>(.+?)</blockquote>", text, re.S).group(1)
    assert state.startswith(f"{emoji.STATUS} وضعیت: در انتظار پرداخت\n")
    assert f"\n{emoji.DEADLINE} مهلت پرداخت: " in state
    assert f"\n{emoji.PRODUCT} محصول: سرویس نمونه، یک ماهه\n" in text
    assert f"\n{emoji.AMOUNT} مبلغ: ۲۰۰,۰۰۰ تومان\n" in text
    assert f"\n{emoji.DATE} تاریخ ثبت: {jalali(order.created_at)}، ساعت " in text
    assert buttons == [
        [
            {"text": f"{emoji.COPY} کپی شماره", "copy_text": {"text": str(order.order_number)}},
            {
                "text": f"{emoji.WEB} صفحه‌ی سفارش",
                "url": settings.SITE_URL + reverse("order-detail", args=[order.order_number]),
            },
        ],
        [{"text": f"{emoji.BACK} بازگشت", "callback_data": "p:l:unpaid"}],
    ]


NOTHING_TO_DO = "کاری لازم نیست."


# The bot names the one account a customer's message should go to.
HANDLE = support.details()["telegram"]


@pytest.mark.parametrize(
    ("state", "card", "sentence"),
    [
        ("awaiting_input", {}, f"اطلاعات فعال‌سازی و شماره‌ی سفارش را برای {HANDLE} بفرستید."),
        (
            "refund_owed",
            {},
            f"شماره‌ی کارت یا شبای به نام خودتان و شماره‌ی سفارش را برای {HANDLE} بفرستید.",
        ),
        ("late", {}, f"زمان تحویل گذشته است. برای پیگیری، به {HANDLE} پیام بدهید."),
        ("replacement", {}, NOTHING_TO_DO),
        ("queued", {}, NOTHING_TO_DO),
        ("delivered", {}, NOTHING_TO_DO),
        ("delivered", {"in_warranty": True}, f"مشکلی هست؟ به {HANDLE} پیام بدهید."),
        ("refunded", {}, NOTHING_TO_DO),
        ("expired", {}, "پرداختی برای این سفارش ثبت نشد."),
        ("cancelled", {}, "پرداختی برای این سفارش ثبت نشده بود."),
        (
            "cancelled",
            {"reason": "operator"},
            f"پرداختی برای این سفارش ثبت نشده بود. دلیلش را از {HANDLE} بپرسید.",
        ),
    ],
)
def test_every_state_names_its_next_step_as_the_order_page_does(buyer, plan, state, card, sentence):
    summary = customer_summary(placed(buyer, plan))
    summary["card"] = {**summary["card"], **order_status.pill(state), **card}
    context = {**summary, "support": support.details(), "order_url": "/"}
    text = render_to_string("telegram/panel_order.html", context)
    assert sentence in text and "تماس بگیرید" not in text
    if card:
        assert NOTHING_TO_DO not in text


@pytest.mark.parametrize(
    ("state", "contact", "offered"),
    [
        ("awaiting_input", True, True),
        ("refund_owed", True, True),
        ("late", True, True),
        ("delivered", True, True),
        ("cancelled", True, True),
        # Writing to support is how an unpaid order is paid, and the bot never offers a payment.
        ("unpaid", True, False),
        ("queued", False, False),
        ("delivered", False, False),
    ],
)
def test_the_order_screen_offers_support_where_the_order_page_does(
    buyer, plan, monkeypatch, state, contact, offered
):
    order = placed(buyer, plan)
    summary = telegram_panel.customer_summary

    def forced(order):
        found = summary(order)
        found["card"] = {**found["card"], **order_status.pill(state), "contact": contact}
        return found

    monkeypatch.setattr(telegram_panel, "customer_summary", forced)
    text, buttons = telegram_panel.render(buyer, f"o:{order.order_number}")
    framed(text, buttons)
    assert ([SUPPORT] in buttons) is offered
    assert buttons[-1][0]["callback_data"].startswith("p:l:")


def test_a_refund_owed_screen_offers_support(buyer, plan, operator):
    order, item = queued(buyer, plan, operator)
    cancel(item, operator)
    _, buttons = telegram_panel.render(buyer, f"o:{order.order_number}")
    assert [SUPPORT] in buttons
    assert buttons[-1] == [{"text": f"{emoji.BACK} بازگشت", "callback_data": "p:l:current"}]


def test_the_bot_writes_the_shop_as_premshop():
    # The wordmark is «PremShop» in Latin everywhere (design-language, Email); the Persian
    # spelling belongs to search aliases only.
    base = django_settings.BASE_DIR
    sources = [base / "apps/accounts/telegram.py", *(base / "templates/telegram").glob("*.html")]
    for path in sources:
        assert "پرم‌شاپ" not in path.read_text(encoding="utf-8"), path


@pytest.mark.parametrize(
    "screen",
    [
        "o:{other}",
        "o:x",
        "o:",
        "o:-1",
        "o:" + "9" * 30,
        "o:²",
        "l:",
        "l:all",
        # The lists' names before they took the site's: an old button lands on the home.
        "l:active",
        "l:closed",
        "orders",
        "nonsense",
    ],
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
        share_url(settings.SITE_URL),
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


def test_an_empty_list_says_so(buyer, settings):
    text, buttons = telegram_panel.render(buyer, "l:current")
    assert order_status.TABS["current"][1] in text
    assert buttons == [all_orders(settings.SITE_URL), BACK_HOME]


def test_every_panel_screen_keeps_the_frame(buyer, plan, operator):
    delivered, item = queued(buyer, plan, operator)
    deliver(item, operator, note=NOTE)
    owed, owed_item = queued(buyer, plan, operator)
    cancel(owed_item, operator)
    unpaid = placed(buyer, plan)
    for order in (delivered, owed, unpaid):
        for screen in screens(order):
            framed(*telegram_panel.render(buyer, screen))


def test_home_isolates_the_half_hidden_number_left_to_right(buyer):
    User.objects.filter(pk=buyer.pk).update(phone="09123456789")
    buyer.refresh_from_db()
    text, _ = telegram_panel.render(buyer, "home")
    assert "\N{LEFT-TO-RIGHT ISOLATE}۰۹۱۲•••۶۷۸۹\N{POP DIRECTIONAL ISOLATE}" in text

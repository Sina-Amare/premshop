"""The screens of the shop bot's panel: a linked customer's own orders, as the site shows them.

The bot's handlers live in accounts, which may not import orders, so the orders app
registers `render` there as it starts. A screen never carries a credential, the
customer's input or a way to pay: Telegram requires its Stars for goods sold inside a
bot, so every payment stays on the site, one link away.
"""

from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.telegram import mask_email
from apps.core import support
from apps.core.formatting import to_persian_digits
from apps.orders import status as order_status
from apps.core import telegram_emoji as emoji
from apps.orders.models import Order
from apps.orders.selectors import customer_orders, customer_summary, order_lead

RECENT = 5
Buttons = list[list[dict[str, Any]]]

# The lists are the site's order groups, so the bot and the order list file an order alike.
LIST_EMOJI = {
    "unpaid": emoji.UNPAID,
    "current": emoji.ACTIVE,
    "delivered": emoji.DELIVERED,
    "cancelled": emoji.CLOSED,
}


def _site(name: str, *args: object) -> str:
    return settings.SITE_URL.rstrip("/") + reverse(name, args=args)


def _list_of(order: Order) -> str:
    return order_status.tab_of(order_lead(order, timezone.now()))


def _emoji_of(order: Order) -> str:
    items = order.items.all()
    return emoji.for_product(items[0].product_snapshot.get("name", "") if items else "")


def render(user: User, screen: str) -> tuple[str, Buttons]:
    """`screen` comes from a button and is never trusted: an order shows only if it is the user's."""
    if screen.startswith("l:") and screen[2:] in order_status.TABS:
        return _list(user, screen[2:])
    number = screen.removeprefix("o:")
    # ASCII only: str.isdigit() also accepts «²», which int() refuses.
    if screen.startswith("o:") and number.isascii() and number.isdigit():
        order = customer_orders(user).filter(order_number=int(number)).first()
        if order is not None:
            return _order(order)
    return _home(user)


def _home(user: User) -> tuple[str, Buttons]:
    text = render_to_string(
        "telegram/panel_home.html", {"email": mask_email(user.email), "e": emoji}
    )
    counts = dict.fromkeys(order_status.TABS, 0)
    for order in customer_orders(user):
        counts[_list_of(order)] += 1
    grid = [
        {
            "text": f"{LIST_EMOJI[key]} {label} {to_persian_digits(counts[key])}",
            "callback_data": f"p:l:{key}",
        }
        for key, (label, _) in order_status.TABS.items()
    ]
    support_url = support.details()["telegram_url"]
    # Telegram's own share sheet: the customer picks a chat and the shop's address goes there.
    share = "https://t.me/share/url?" + urlencode({"url": _site("home")})
    return text.strip(), [
        grid[:2],
        grid[2:],
        [{"text": f"{emoji.BUY} خرید از سایت", "url": _site("home"), "style": "primary"}],
        [
            {"text": f"{emoji.ACCOUNT} حساب کاربری", "url": _site("account")},
            {"text": f"{emoji.SUPPORT} پشتیبانی", "url": support_url},
        ],
        [{"text": f"{emoji.SHARE} معرفی PremShop", "url": share}],
    ]


def _list(user: User, key: str) -> tuple[str, Buttons]:
    label, empty = order_status.TABS[key]
    rows = []
    for order in customer_orders(user).order_by("-pk"):
        if _list_of(order) != key:
            continue
        summary = customer_summary(order)
        lines = [line for group in summary["groups"] for line in group["lines"]]
        names = list(dict.fromkeys(line["facts"]["name"] for line in lines))
        rows.append(
            {"order": order, "emoji": _emoji_of(order), "status": summary["status"], "names": names}
        )
        if len(rows) == RECENT:
            break
    text = render_to_string(
        "telegram/panel_orders.html",
        {"icon": LIST_EMOJI[key], "label": label, "empty": empty, "rows": rows, "e": emoji},
    )
    buttons = [
        {
            "text": f"{row['emoji']} سفارش {to_persian_digits(row['order'].order_number)}",
            "callback_data": f"p:o:{row['order'].order_number}",
        }
        for row in rows
    ]
    # Two to a row: five full-width buttons push the message off a phone's screen.
    grid = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return text.strip(), [
        *grid,
        [{"text": f"{emoji.WEB} همه‌ی سفارش‌ها در سایت", "url": _site("order-list")}],
        [{"text": f"{emoji.BACK} بازگشت", "callback_data": "p:home"}],
    ]


def _order(order: Order) -> tuple[str, Buttons]:
    summary = customer_summary(order)
    contact = support.details()
    text = render_to_string(
        "telegram/panel_order.html",
        {**summary, "support": contact, "emoji": _emoji_of(order), "e": emoji},
    )
    copy = {"text": f"{emoji.COPY} کپی شماره", "copy_text": {"text": str(order.order_number)}}
    page = {"text": f"{emoji.WEB} صفحه‌ی سفارش", "url": _site("order-detail", order.order_number)}
    buttons: Buttons = [[copy, page]]
    card = summary["card"]
    # Support is offered where the order page offers it, except on an unpaid order, which is
    # paid through support: a bot may not offer a way to pay. Where it is offered, it is the
    # customer's next move, so it is the screen's one coloured button.
    if card["contact"] and card["state"] != "unpaid":
        buttons.append(
            [
                {
                    "text": f"{emoji.SUPPORT} پیام به پشتیبانی",
                    "url": contact["telegram_url"],
                    "style": "primary",
                }
            ]
        )
    back = {"text": f"{emoji.BACK} بازگشت", "callback_data": f"p:l:{_list_of(order)}"}
    return text.strip(), [*buttons, [back]]

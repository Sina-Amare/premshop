"""The screens of the shop bot's panel: a linked customer's own orders, as the site shows them.

The bot's handlers live in accounts, which may not import orders, so the orders app
registers `render` there as it starts. A screen never carries a credential, the
customer's input or a way to pay: Telegram requires its Stars for goods sold inside a
bot, so every payment stays on the site, one link away.
"""

from typing import Any

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.telegram import mask_email
from apps.core import support
from apps.core.formatting import to_persian_digits
from apps.orders.models import Order
from apps.orders.selectors import customer_orders, customer_summary, open_orders

RECENT = 5
Buttons = list[list[dict[str, Any]]]


def _site(name: str, *args: object) -> str:
    return settings.SITE_URL.rstrip("/") + reverse(name, args=args)


def render(user: User, screen: str) -> tuple[str, Buttons]:
    """`screen` comes from a button and is never trusted: an order shows only if it is the user's."""
    if screen == "orders":
        return _orders(user)
    number = screen.removeprefix("o:")
    if screen.startswith("o:") and number.isdigit():
        order = customer_orders(user).filter(order_number=int(number)).first()
        if order is not None:
            return _order(order)
    return _home(user)


def _home(user: User) -> tuple[str, Buttons]:
    text = render_to_string(
        "telegram/panel_home.html",
        {
            "email": mask_email(user.email),
            "open": open_orders(user).count(),
            "account_url": _site("account"),
        },
    )
    return text.strip(), [
        [
            {"text": "سفارش‌های من", "callback_data": "p:orders"},
            {"text": "پشتیبانی", "url": f"https://t.me/{support.TELEGRAM_HANDLE}"},
        ]
    ]


def _orders(user: User) -> tuple[str, Buttons]:
    rows = []
    for order in customer_orders(user).order_by("-pk")[:RECENT]:
        summary = customer_summary(order)
        lines = [line for group in summary["groups"] for line in group["lines"]]
        names = list(dict.fromkeys(line["facts"]["name"] for line in lines))
        rows.append({"order": order, "status": summary["status"], "names": names})
    text = render_to_string(
        "telegram/panel_orders.html", {"rows": rows, "orders_url": _site("order-list")}
    )
    buttons = [
        {
            "text": f"سفارش {to_persian_digits(row['order'].order_number)}",
            "callback_data": f"p:o:{row['order'].order_number}",
        }
        for row in rows
    ]
    # Two to a row: five full-width buttons push the message off a phone's screen.
    grid = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return text.strip(), [*grid, [{"text": "بازگشت", "callback_data": "p:home"}]]


def _order(order: Order) -> tuple[str, Buttons]:
    text = render_to_string(
        "telegram/panel_order.html",
        {**customer_summary(order), "order_url": _site("order-detail", order.order_number)},
    )
    return text.strip(), [
        [{"text": "کپی شماره‌ی سفارش", "copy_text": {"text": str(order.order_number)}}],
        [{"text": "بازگشت به سفارش‌ها", "callback_data": "p:orders"}],
    ]

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
        [{"text": "سفارش‌های من", "callback_data": "p:orders"}],
        [{"text": "پشتیبانی", "url": f"https://t.me/{support.TELEGRAM_HANDLE}"}],
    ]


def _orders(user: User) -> tuple[str, Buttons]:
    orders = customer_orders(user).order_by("-pk")[:RECENT]
    summaries = [customer_summary(order) for order in orders]
    text = render_to_string(
        "telegram/panel_orders.html",
        {"summaries": summaries, "orders_url": _site("order-list")},
    )
    rows = [
        [
            {
                "text": f"سفارش {to_persian_digits(summary['order'].order_number)}",
                "callback_data": f"p:o:{summary['order'].order_number}",
            }
        ]
        for summary in summaries
    ]
    return text.strip(), [*rows, [{"text": "بازگشت", "callback_data": "p:home"}]]


def _order(order: Order) -> tuple[str, Buttons]:
    text = render_to_string(
        "telegram/panel_order.html",
        {**customer_summary(order), "order_url": _site("order-detail", order.order_number)},
    )
    return text.strip(), [
        [{"text": "کپی شماره‌ی سفارش", "copy_text": {"text": str(order.order_number)}}],
        [{"text": "بازگشت به سفارش‌ها", "callback_data": "p:orders"}],
    ]

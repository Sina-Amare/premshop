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
from apps.orders import telegram_emoji as emoji
from apps.orders.models import Order, OrderItem
from apps.orders.selectors import customer_orders, customer_summary

RECENT = 5
Buttons = list[list[dict[str, Any]]]
S = OrderItem.Status

# An order sits in the first list holding one of its items' statuses: a paid order with
# one item still at work is «در جریان», and one delivered beside a refund is delivered.
LISTS = {
    "unpaid": ("منتظر پرداخت", emoji.UNPAID, {S.PENDING_PAYMENT}),
    "active": ("در جریان", emoji.ACTIVE, {S.QUEUED, S.AWAITING_INPUT, S.REPLACEMENT_REQUESTED}),
    "delivered": ("تحویل‌شده", emoji.DELIVERED, {S.DELIVERED}),
    "closed": ("لغو و بازپرداخت", emoji.CLOSED, {S.CANCELLED, S.REFUNDED}),
}


def _site(name: str, *args: object) -> str:
    return settings.SITE_URL.rstrip("/") + reverse(name, args=args)


def list_for(statuses: set[str]) -> str:
    return next((key for key, (_, _, held) in LISTS.items() if statuses & held), "closed")


def _list_of(order: Order) -> str:
    return list_for({item.status for item in order.items.all()})


def _emoji_of(order: Order) -> str:
    items = order.items.all()
    return emoji.for_product(items[0].product_snapshot.get("name", "") if items else "")


def render(user: User, screen: str) -> tuple[str, Buttons]:
    """`screen` comes from a button and is never trusted: an order shows only if it is the user's."""
    if screen.startswith("l:") and screen[2:] in LISTS:
        return _list(user, screen[2:])
    number = screen.removeprefix("o:")
    # ASCII only: str.isdigit() also accepts «²», which int() refuses.
    if screen.startswith("o:") and number.isascii() and number.isdigit():
        order = customer_orders(user).filter(order_number=int(number)).first()
        if order is not None:
            return _order(order)
    return _home(user)


def _home(user: User) -> tuple[str, Buttons]:
    text = render_to_string("telegram/panel_home.html", {"email": mask_email(user.email)})
    counts = dict.fromkeys(LISTS, 0)
    for order in customer_orders(user):
        counts[_list_of(order)] += 1
    grid = [
        {
            "text": f"{icon} {label} {to_persian_digits(counts[key])}",
            "callback_data": f"p:l:{key}",
        }
        for key, (label, icon, _) in LISTS.items()
    ]
    support_url = f"https://t.me/{support.TELEGRAM_HANDLE}"
    return text.strip(), [
        grid[:2],
        grid[2:],
        [{"text": f"{emoji.BUY} خرید از سایت", "url": _site("home")}],
        [
            {"text": f"{emoji.ACCOUNT} حساب کاربری", "url": _site("account")},
            {"text": f"{emoji.SUPPORT} پشتیبانی", "url": support_url},
        ],
    ]


def _list(user: User, key: str) -> tuple[str, Buttons]:
    label, icon, _ = LISTS[key]
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
        {"icon": icon, "label": label, "rows": rows, "orders_url": _site("order-list")},
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
    return text.strip(), [*grid, [{"text": f"{emoji.BACK} بازگشت", "callback_data": "p:home"}]]


def _order(order: Order) -> tuple[str, Buttons]:
    text = render_to_string(
        "telegram/panel_order.html",
        {
            **customer_summary(order),
            "support": support.details(),
            "emoji": _emoji_of(order),
            "order_url": _site("order-detail", order.order_number),
        },
    )
    copy = {
        "text": f"{emoji.COPY} کپی شماره‌ی سفارش",
        "copy_text": {"text": str(order.order_number)},
    }
    back = {"text": f"{emoji.BACK} بازگشت", "callback_data": f"p:l:{_list_of(order)}"}
    return text.strip(), [[copy], [back]]

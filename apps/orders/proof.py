"""Independent, session-bound permission to read an owned order's delivery."""

import time
from datetime import datetime
from typing import cast

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils.crypto import salted_hmac
from django.utils.html import format_html

from apps.accounts import otp
from apps.accounts.models import User
from apps.core import ratelimit, telegram
from apps.core.email import send_templated_email
from apps.core.formatting import to_persian_digits
from apps.orders.models import Order, OrderEmailRecovery

TTL = 600
UNSENT = "کد فرستاده نشد. چند دقیقه دیگر دوباره امتحان کنید."
LIMITED = "زیاد کد خواسته‌اید. کمی بعد دوباره امتحان کنید."
LAPSED = "تأیید ایمیل فقط تا ۱۰ دقیقه معتبر است. برای دیدن اطلاعات تحویل، دوباره کد بگیرید."
RECOVERY_UNAVAILABLE = "بازیابی فقط با تلگرامی ممکن است که پیش از خرید به حساب وصل بوده است."


def locked_order(order_id: int, actor: User, *, operator: bool = False) -> tuple[Order, User]:
    account = User.objects.filter(pk=actor.pk, is_active=True).first()
    if account is None or (
        operator and not (account.is_staff and account.has_perm("orders.change_order"))
    ):
        raise PermissionDenied
    orders = Order.objects.filter(pk=order_id)
    if not operator:
        orders = orders.filter(user_id=actor.pk)
    owner_id = orders.values_list("user_id", flat=True).first()
    if owner_id is None:
        raise PermissionDenied
    owner = User.objects.select_for_update(no_key=True).get(pk=owner_id)
    return orders.select_for_update().get(), owner


def email_for(order: Order) -> tuple[str, int]:
    recovery = OrderEmailRecovery.objects.filter(order=order).order_by("-pk").first()
    return (recovery.email, recovery.pk) if recovery else (order.placement_email, 0)


def _subject(order: Order, session_key: str) -> str:
    email, generation = email_for(order)
    if not session_key or not email:
        raise ValidationError("برای دیدن اطلاعات تحویل، به پشتیبانی پیام بدهید.")
    binding = salted_hmac(
        "order-delivery", f"{order.user_id}:{session_key}:{email}:{generation}"
    ).hexdigest()
    return f"order-delivery:{order.pk}:{binding}"


def _budget(order: Order, actor: User, prefix: str, ip: str | None = None) -> None:
    limits = [
        (f"{prefix}:account", str(actor.pk)),
        (f"{prefix}:order", str(order.pk)),
    ]
    for name, subject in limits:
        for times, seconds in ((1, 60), (5, 3600)):
            if not ratelimit.hit(ratelimit.Limit(f"{name}:{seconds}", times, seconds), subject):
                raise ValidationError(LIMITED)
    if ip is not None and not ratelimit.hit(ratelimit.Limit(f"{prefix}:ip", 10, 3600), ip):
        raise ValidationError(LIMITED)


def request_code(*, order_id: int, actor: User, session_key: str, ip: str) -> None:
    with transaction.atomic():
        order, _ = locked_order(order_id, actor)
        subject = _subject(order, session_key)
        email, _ = email_for(order)
        _budget(order, actor, "delivery-code", ip)
    code = otp._new_code()
    try:
        sent = send_templated_email(
            "delivery_code",
            to=[email],
            context={"code": code, "order_number": order.order_number},
        )
    except Exception:  # noqa: BLE001 — transport errors can include a recipient or message
        raise ValidationError(UNSENT) from None
    if not sent:
        raise ValidationError(UNSENT)
    with transaction.atomic():
        order, _ = locked_order(order_id, actor)
        if subject != _subject(order, session_key):
            raise ValidationError(UNSENT)
        otp._store(subject, code)


def confirm_code(*, order_id: int, actor: User, session_key: str, code: str) -> bool:
    with transaction.atomic():
        order, _ = locked_order(order_id, actor)
        subject = _subject(order, session_key)
        if otp.consume(subject, code) is None:
            return False
        cache.set(f"{subject}:proof", int(time.time()) + TTL, timeout=TTL)
        return is_confirmed(order=order, session_key=session_key)


def is_confirmed(*, order: Order, session_key: str) -> bool:
    try:
        return int(cache.get(f"{_subject(order, session_key)}:proof", 0)) > time.time()
    except ValidationError:
        return False


def _recovery_subject(order: Order, owner: User, actor: User, email: str) -> str:
    if (
        not owner.telegram_id
        or not owner.telegram_linked_at
        or owner.telegram_linked_at >= order.created_at
    ):
        raise ValidationError(RECOVERY_UNAVAILABLE)
    validate_email(email)
    _, generation = email_for(order)
    binding = salted_hmac(
        "order-recovery",
        f"{actor.pk}:{email}:{owner.telegram_id}:{owner.telegram_linked_at.isoformat()}:{generation}",
    ).hexdigest()
    return f"order-recovery:{order.pk}:{binding}"


def start_recovery(*, order_id: int, actor: User, email: str) -> None:
    email = otp.normalize_email(email)
    with transaction.atomic():
        order, owner = locked_order(order_id, actor, operator=True)
        subject = _recovery_subject(order, owner, actor, email)
        chat_id = cast(int, owner.telegram_id)
        _budget(order, actor, "delivery-recovery")
    code = otp._new_code()
    text = format_html(
        "کد تغییر ایمیل سفارش {}: <code>{}</code>\n"
        "ایمیل جدید: <code>{}</code>\n"
        "اگر این تغییر را از پشتیبانی خواسته‌اید، کد را برایش بخوانید.\n"
        "کد تا ۱۰ دقیقه معتبر است؛ کد ورود به سایت را برای پشتیبانی نفرستید.",
        to_persian_digits(order.order_number),
        code,
        email,
    )
    try:
        telegram.send_message(chat_id, str(text))
    except Exception:  # noqa: BLE001 — never echo transport payloads in command errors
        raise ValidationError(UNSENT) from None
    with transaction.atomic():
        order, owner = locked_order(order_id, actor, operator=True)
        if subject != _recovery_subject(order, owner, actor, email):
            raise ValidationError(RECOVERY_UNAVAILABLE)
        otp._store(subject, code)


def finish_recovery(*, order_id: int, actor: User, email: str, code: str) -> bool:
    email = otp.normalize_email(email)
    with transaction.atomic():
        order, owner = locked_order(order_id, actor, operator=True)
        subject = _recovery_subject(order, owner, actor, email)
        if otp.consume(subject, code) is None:
            return False
        OrderEmailRecovery.objects.create(
            order=order,
            email=email,
            operator=actor,
            telegram_id=cast(int, owner.telegram_id),
            telegram_linked_at=cast(datetime, owner.telegram_linked_at),
        )
    return True

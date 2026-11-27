"""Independent, session-bound permission to read an owned order's delivery."""

import logging
import time
from datetime import datetime
from typing import cast

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.crypto import salted_hmac
from django.utils.html import format_html

from apps.accounts import otp
from apps.accounts import services as account_services
from apps.accounts.models import User
from apps.core import ratelimit, sms, telegram
from apps.core.email import send_templated_email
from apps.core.formatting import to_persian_digits
from apps.orders.models import Order, OrderEmailRecovery

logger = logging.getLogger(__name__)
TTL = 600
UNSENT = "کد به ایمیل سفارش فرستاده نشد. چند دقیقه دیگر دوباره کد بگیرید."
SMS_UNSENT = "نتوانستیم کد را پیامک کنیم. چند دقیقه دیگر دوباره کد بگیرید."
NO_EMAIL = "برای این سفارش ایمیلی ثبت نشده است."
NO_PHONE = "برای این سفارش نمی‌توانیم کد پیامک کنیم."
CHANGED = "ایمیل یا شماره‌ی این سفارش همین حالا تغییر کرد. کد جدیدی بگیرید."
LIMITED = "درخواست کد موقتاً محدود شده است. کمی بعد دوباره امتحان کنید."
LAPSED = "مهلت دیدن اطلاعات تحویل تمام شد. برای دیدن دوباره، کد جدیدی بگیرید."
DELIVERY_LIMITED = (
    "فعلاً نمی‌توانیم کد دیگری بفرستیم. اگر در همین مرورگر برای این سفارش در ۱۰ دقیقه‌ی گذشته کد گرفته‌اید"
    " و هنوز از آن استفاده نکرده‌اید، آخرین کد را وارد کنید. در غیر این صورت، کمی بعد دوباره کد بگیرید."
)
RECOVERY_UNAVAILABLE = (
    "برای بازیابی باید از همان حساب تلگرامی استفاده کنید که پیش از خرید به حساب کاربری وصل بود."
)


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


def phone_for(order: Order) -> str:
    """The number the order was placed with, while it is still the account's (ADR-0031).

    A number support changes later never receives a code for an earlier order, as the
    email has its own guarded recovery."""
    current = User.objects.filter(pk=order.user_id).values_list("phone", flat=True).first()
    return current if current and current == order.placement_phone else ""


def _instead(email: str) -> str:
    """Where a buyer turns when no SMS can go: the order email, or support without one."""
    return "کد را به ایمیل سفارش بگیرید." if email else "به پشتیبانی پیام بدهید."


def _sms_budget(phone: str, *, email: str) -> None:
    """Delivery codes spend the sign-in SMS's daily budgets: each one is paid for."""
    then = _instead(email)
    if not ratelimit.remaining(ratelimit.SMS_DAILY_BUDGET, "shop"):
        raise ValidationError(f"فعلاً نمی‌توانیم کد را پیامک کنیم. {then}")
    if not ratelimit.remaining(ratelimit.OTP_REQUESTS_PER_PHONE_DAILY, phone):
        daily = to_persian_digits(ratelimit.OTP_REQUESTS_PER_PHONE_DAILY.times)
        raise ValidationError(f"برای این شماره در ۲۴ ساعت فقط {daily} کد پیامک می‌کنیم. {then}")


def _subject(order: Order, session_key: str) -> str:
    email, generation = email_for(order)
    phone = phone_for(order)
    if not session_key or not (email or phone):
        raise ValidationError("برای دیدن اطلاعات تحویل، به پشتیبانی پیام بدهید.")
    # A new order email or a new account number ends every proof made before it.
    binding = salted_hmac(
        "order-delivery", f"{order.user_id}:{session_key}:{email}:{generation}:{phone}"
    ).hexdigest()
    return f"order-delivery:{order.pk}:{binding}"


def _budget(
    order: Order, actor: User, prefix: str, ip: str | None = None, message: str = LIMITED
) -> None:
    limits = [
        (f"{prefix}:account", str(actor.pk)),
        (f"{prefix}:order", str(order.pk)),
    ]
    for name, subject in limits:
        for times, seconds in ((1, 60), (5, 3600)):
            if not ratelimit.hit(ratelimit.Limit(f"{name}:{seconds}", times, seconds), subject):
                raise ValidationError(message)
    if ip is not None and not ratelimit.hit(ratelimit.Limit(f"{prefix}:ip", 10, 3600), ip):
        raise ValidationError(message)


def request_code(
    *, order_id: int, actor: User, session_key: str, ip: str, channel: str = "email"
) -> None:
    """Send a code to the order's email or, with channel "sms", to the account's number."""
    if channel not in ("email", "sms"):
        raise ValidationError(UNSENT)
    with transaction.atomic():
        order, _ = locked_order(order_id, actor)
        subject = _subject(order, session_key)
        email, _ = email_for(order)
        to = email if channel == "email" else phone_for(order)
        if not to:
            raise ValidationError(
                NO_EMAIL if channel == "email" else f"{NO_PHONE} {_instead(email)}"
            )
        if channel == "sms":
            _sms_budget(to, email=email)
        _budget(order, actor, "delivery-code", ip, DELIVERY_LIMITED)
    code = otp._new_code()
    if channel == "sms":
        try:
            sms.send_code(to, code, kind="delivery_code")
        except sms.SmsNotSent as error:
            # SMS.ir's statuses only: its replies can repeat the number and the code.
            logger.error(
                "delivery code SMS not sent: HTTP %s, SMS.ir status %s",
                error.status or "none",
                "none" if error.provider_status is None else error.provider_status,
            )
            raise ValidationError(SMS_UNSENT) from None
        account_services.record_sms_sent(to)
    else:
        try:
            sent = send_templated_email(
                "delivery_code",
                to=[to],
                context={"code": code, "order_number": order.order_number},
            )
        except Exception:  # noqa: BLE001 — transport errors can include a recipient or message
            raise ValidationError(UNSENT) from None
        if not sent:
            raise ValidationError(UNSENT)
    with transaction.atomic():
        order, _ = locked_order(order_id, actor)
        if subject != _subject(order, session_key):
            # The code went out but is not stored, so it would not work: ask for a new one.
            raise ValidationError(CHANGED)
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
    otp.normalize_gmail(email, required=True)
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
        "اگر خودتان از پشتیبانی خواسته‌اید ایمیل این سفارش عوض شود و ایمیل بالا درست است، این کد را"
        " برای پشتیبانی بفرستید؛ وگرنه آن را به کسی ندهید.\n"
        "کد تغییر ایمیل تا ۱۰ دقیقه اعتبار دارد. کد ورود به سایت را برای پشتیبانی نفرستید.",
        to_persian_digits(order.order_number),
        code,
        email,
    )
    try:
        telegram.send_message(chat_id, str(text))
    except Exception:  # noqa: BLE001 — never echo transport payloads in command errors
        raise ValidationError(
            "کد تغییر ایمیل در تلگرام فرستاده نشد. چند دقیقه دیگر دوباره امتحان کنید."
        ) from None
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

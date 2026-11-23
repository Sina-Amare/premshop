import logging
from datetime import datetime, timedelta
from smtplib import SMTPException, SMTPRecipientsRefused, SMTPResponseException
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import CharField, Exists, OuterRef, Value
from django.db.models.functions import Cast, Concat
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.core import telegram
from apps.core import telegram_emoji as emoji
from apps.core.email import render_email, send_templated_email
from apps.core.email_log import MailQuotaExceeded
from apps.core.richtext import absolute_markdown_links
from apps.notifications.models import ConfirmationSweep, Notification
from apps.orders import status as order_status
from apps.orders.models import Order, OrderItem, OrderItemEvent

logger = logging.getLogger(__name__)
_DELAYS = (60, 300, 900, 3600, 21600)


def _order_url(order_number: int) -> str:
    return settings.SITE_URL.rstrip("/") + reverse("order-detail", args=[order_number])


def _order_button(order: Order) -> list[list[dict[str, str]]]:
    # A row keeps only its text, so a notice's button is built as it is sent.
    text = f"{emoji.ORDER} مشاهده‌ی سفارش"
    return [[{"text": text, "url": _order_url(order.order_number), "style": "primary"}]]


def _key(template_name: str, occurrence_id: int, user_id: int, channel: str) -> str:
    return f"{template_name}:{occurrence_id}:user:{user_id}:{channel}"


def _occurrence(key: str) -> int:
    """The history row a notice was made for, read back from `_key`."""
    return int(key.split(":")[1])


def _superseded(event_id: int) -> bool:
    """A delay's item has changed since: a pause, a details request, a delivery, a
    cancellation or a newer delay. The new time the notice names is no longer true."""
    return OrderItemEvent.objects.filter(order_item__events__pk=event_id, pk__gt=event_id).exists()


def _stale_delay(row: Notification) -> bool:
    """Locks the delay's item until this send commits, so nothing can change it between
    this check and the provider's answer: a holiday that starts meanwhile waits."""
    event_id = _occurrence(row.dedupe_key)
    item_id = OrderItemEvent.objects.filter(pk=event_id).values_list("order_item_id", flat=True)
    list(OrderItem.objects.select_for_update().filter(pk__in=item_id).values_list("pk", flat=True))
    return _superseded(event_id)


def _drop(row: Notification) -> None:
    # Not an attempt: nothing was sent, and nothing should be sent again.
    row.status, row.next_attempt_at, row.last_error_class = "failed", None, "superseded"


def drop_superseded_delay_notices() -> int:
    """The minute run's sweep: a superseded delay fails now, not at its next retry."""
    dropped = 0
    with transaction.atomic():
        pending = Notification.objects.select_for_update(skip_locked=True).filter(
            template_name=Notification.Template.DELAY, status=Notification.Status.PENDING
        )
        for row in pending:
            if _superseded(_occurrence(row.dedupe_key)):
                _drop(row)
                row.save(update_fields=["status", "next_attempt_at", "last_error_class"])
                dropped += 1
    return dropped


def enqueue_event(
    template_name: str,
    occurrence_id: int,
    order_id: int,
    *,
    item_id: int | None = None,
    due_at: datetime | None = None,
) -> Notification | None:
    if (
        template_name not in Notification.Template.values
        or template_name == "purchase_confirmation"
    ):
        raise ValueError("Unknown order notification")
    if template_name == "customer_claim":
        return _enqueue_customer_claim(occurrence_id, order_id, item_id)
    if template_name == Notification.Template.DELAY and _superseded(occurrence_id):
        return None
    order = Order.objects.values(
        "user_id",
        "user__email",
        "user__telegram_id",
        "placement_email",
        "order_number",
        "total_amount",
    ).get(pk=order_id)
    context: dict[str, Any] = {
        "order_number": order["order_number"],
        "order_url": _order_url(order["order_number"]),
        "amount": order["total_amount"],
        "due_at": due_at,
    }
    rows = OrderItem.objects.filter(order_id=order_id).select_related("order", "refund")
    rows = rows.defer("customer_input")
    items = [rows.get(pk=item_id)] if item_id is not None else list(rows)
    if item_id is not None:
        facts = items[0].product_snapshot
        context.update(
            product_name=facts["name"],
            plan_title=facts["plan_title"],
            cancel_reason=items[0].cancel_reason,
        )
    elif template_name == "item_cancelled":
        # An unpaid order's cancellation: no payment, so no refund line, and every product.
        context["products"] = list(dict.fromkeys(one.product_snapshot["name"] for one in items))
    if template_name in ("item_cancelled", "item_refunded"):
        refund = getattr(items[0], "refund", None) if item_id is not None else None
        context["amount"] = refund.amount if refund is not None else 0
    # The state the message reports, in the site's words: its item's, or for a message about
    # the whole order, the state the order leads with.
    now = timezone.now()
    context["pill_state"] = order_status.lead_state(
        [
            order_status.item_state(
                one, getattr(one, "refund", None), one.order.unpaid_expires_at, now
            )
            for one in items
        ]
    )
    recipient = order["placement_email"] or order["user__email"]
    subject, text, html = render_email(template_name, context)
    # A customer who signed in by phone and gave no email hears through Telegram, or
    # from the order page.
    row = None
    if recipient:
        row, _ = Notification.objects.get_or_create(
            dedupe_key=_key(template_name, occurrence_id, order["user_id"], "email"),
            defaults={
                "order_id": order_id,
                "template_name": template_name,
                "recipient": recipient,
                "subject": subject,
                "text_body": text,
                "html_body": html,
            },
        )
    # After the email row, so a Telegram problem can never cost the email.
    if settings.TELEGRAM_BOT_TOKEN and order["user__telegram_id"]:
        Notification.objects.get_or_create(
            dedupe_key=_key(template_name, occurrence_id, order["user_id"], "telegram"),
            defaults={
                "order_id": order_id,
                "template_name": template_name,
                "channel": Notification.Channel.TELEGRAM,
                "chat_id": order["user__telegram_id"],
                "text_body": render_to_string(
                    "telegram/order_update.html",
                    {
                        **context,
                        "subject": subject,
                        "template_name": template_name,
                        "icon": emoji.for_product(items[0].product_snapshot.get("name", "")),
                        "e": emoji,
                    },
                ).strip(),
            },
        )
    # Send now instead of at the next minute's run, which stays the retry. Imported here
    # because the tasks module imports this one.
    from apps.notifications.tasks import send_order_notifications

    try:
        send_order_notifications()
    except Exception as error:  # noqa: BLE001 - both rows are saved; the minute run sends them
        logger.warning("send now not queued for order %s: %s", order_id, type(error).__name__)
    return row


def _enqueue_customer_claim(
    event_id: int, order_id: int, item_id: int | None
) -> Notification | None:
    if not settings.OPERATOR_ALERT_EMAIL.strip():
        logger.warning(
            "Customer claim alert disabled: OPERATOR_ALERT_EMAIL is blank; event=%s", event_id
        )
        return None
    facts = (
        OrderItemEvent.objects.filter(
            pk=event_id,
            order_item_id=item_id,
            order_item__order_id=order_id,
            actor=OrderItemEvent.Actor.CUSTOMER,
            from_status=OrderItem.Status.DELIVERED,
            to_status=OrderItem.Status.REPLACEMENT_REQUESTED,
            claim_reason__in=OrderItemEvent.ClaimReason.values,
        )
        .values(
            "claim_reason",
            "created_at",
            "order_item__product_snapshot__name",
            "order_item__order__order_number",
        )
        .first()
    )
    if facts is None:
        return None
    context = {
        "reason": OrderItemEvent.ClaimReason(facts["claim_reason"]).label,
        "order_number": facts["order_item__order__order_number"],
        "product_name": facts["order_item__product_snapshot__name"],
        "at": facts["created_at"],
        "panel_url": settings.SITE_URL.rstrip("/")
        + reverse("admin:orders_order_change", args=[order_id]),
    }
    subject, text, html = render_email("customer_claim", context)
    row, _ = Notification.objects.get_or_create(
        dedupe_key=f"customer_claim:{event_id}:operator:email",
        defaults={
            "order_id": order_id,
            "template_name": "customer_claim",
            "recipient": settings.OPERATOR_ALERT_EMAIL.strip(),
            "subject": subject,
            "text_body": text,
            "html_body": html,
        },
    )
    from apps.notifications.tasks import send_order_notifications

    try:
        send_order_notifications()
    except Exception as error:  # noqa: BLE001 - the minute run retries the saved alert
        logger.warning("Claim sender not queued event=%s error=%s", event_id, type(error).__name__)
    return row


def discover_customer_claims() -> int:
    if not settings.OPERATOR_ALERT_EMAIL.strip():
        return 0
    existing = Notification.objects.filter(
        dedupe_key=Concat(
            Value("customer_claim:"), Cast(OuterRef("pk"), CharField()), Value(":operator:email")
        )
    )
    claims = (
        OrderItemEvent.objects.filter(
            actor="customer",
            to_status="REPLACEMENT_REQUESTED",
            claim_reason__in=OrderItemEvent.ClaimReason.values,
        )
        .filter(~Exists(existing))
        .order_by("pk")
    )
    created = 0
    for event_id, order_id, item_id in claims.values_list(
        "pk", "order_item__order_id", "order_item_id"
    ).iterator():
        try:
            created += _enqueue_customer_claim(event_id, order_id, item_id) is not None
        except Exception as error:  # noqa: BLE001 - one claim must not hold back the rest
            logger.error(
                "Claim alert discovery failed event=%s error=%s", event_id, type(error).__name__
            )
    return created


def confirmation_context(order: Order) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    for facts, price in order.items.order_by("pk").values_list(
        "product_snapshot", "price_snapshot"
    ):
        line = next(
            (line for line in lines if line["snapshot"] == facts and line["price"] == price), None
        )
        if line is not None:
            line["quantity"] += 1
            line["amount"] += price
            continue
        saved = {
            key: facts.get(key, "")
            for key in (
                "name",
                "plan_title",
                "duration_days",
                "delivery_hours",
                "telegram_required",
            )
        }
        saved.update(
            {
                key: absolute_markdown_links(facts.get(key, ""))
                for key in ("full_description", "usage_notes", "refund_note", "delivery_note")
            }
        )
        saved["specs"] = [
            {"title": spec["title"], "value": spec["value"]} for spec in facts.get("specs", [])
        ]
        saved["sections"] = [
            {"title": section.get("title", ""), "body": absolute_markdown_links(section["body"])}
            for section in facts.get("sections", [])
        ]
        lines.append(
            {
                "snapshot": facts,
                "facts": saved,
                "price": price,
                "quantity": 1,
                "amount": price,
                "warranty": str(dict(Product.Warranty.choices).get(facts.get("warranty"), "")),
                "delivery": str(
                    dict(Product.DeliveryType.choices).get(facts.get("delivery_type"), "")
                ),
            }
        )
    # Only projected purchase fields reach templates; inputs and credentials are never read.
    return {
        "order_number": order.order_number,
        "created_at": order.created_at,
        "order_url": settings.SITE_URL.rstrip("/")
        + reverse("order-detail", args=[order.order_number]),
        "lines": [
            {key: value for key, value in line.items() if key != "snapshot"} for line in lines
        ],
        "policies": [
            {"title": page["title"], "body": absolute_markdown_links(page["body"])}
            for page in order.policy_snapshot.values()
        ],
        "subtotal": order.subtotal,
        "discount_amount": order.discount_amount,
        "total_amount": order.total_amount,
    }


def render_confirmation(order: Order) -> tuple[str, str, str]:
    return render_email("purchase_confirmation", confirmation_context(order))


def discover_confirmations() -> int:
    existing = Notification.objects.filter(
        order_id=OuterRef("pk"), template_name="purchase_confirmation"
    )
    ids = (
        Order.objects.exclude(placement_email="")
        .filter(~Exists(existing))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    created = 0
    for order_id in ids.iterator():
        try:
            with transaction.atomic():
                order = Order.objects.only(
                    "placement_email",
                    "order_number",
                    "created_at",
                    "policy_snapshot",
                    "subtotal",
                    "discount_amount",
                    "total_amount",
                ).get(pk=order_id)
                subject, text, html = render_confirmation(order)
                _, fresh = Notification.objects.get_or_create(
                    dedupe_key=f"purchase-confirmation:{order.pk}:email",
                    defaults={
                        "order": order,
                        "recipient": order.placement_email,
                        "subject": subject,
                        "text_body": text,
                        "html_body": html,
                    },
                )
                created += fresh
        except (
            Exception
        ) as error:  # noqa: BLE001 — retry this order without blocking other purchases
            logger.error(
                "confirmation creation failed order=%s error=%s", order_id, type(error).__name__
            )
    return created


def send_due_notifications() -> int:
    ConfirmationSweep.objects.get_or_create(pk=1)
    sent = 0
    while True:
        # Each result commits before the next SMTP call; a crash may duplicate only its current row.
        with transaction.atomic():
            sweep = ConfirmationSweep.objects.select_for_update().get(pk=1)
            now = timezone.now()
            if sweep.quota_wait_until and sweep.quota_wait_until > now:
                return sent
            row = (
                Notification.objects.select_for_update(skip_locked=True)
                .filter(channel="email", status="pending", next_attempt_at__lte=now)
                .order_by("next_attempt_at", "pk")
                .first()
            )
            if row is None:
                return sent
            if row.template_name == Notification.Template.DELAY and _stale_delay(row):
                _drop(row)
                row.save()
                continue
            try:
                accepted = send_templated_email(
                    row.template_name,
                    to=[row.recipient],
                    context={},
                    rendered_parts=(row.subject, row.text_body, row.html_body),
                )
                if not accepted:
                    raise SMTPException("Order email was not accepted")
            except MailQuotaExceeded:
                wait = timezone.now() + timedelta(minutes=15)
                sweep.quota_wait_until = wait
                sweep.save(update_fields=["quota_wait_until"])
                Notification.objects.filter(
                    channel="email", status="pending", next_attempt_at__lte=timezone.now()
                ).update(next_attempt_at=wait)
                return sent
            except Exception as error:  # noqa: BLE001 - keep only the class and numeric SMTP status
                row.last_error_class = type(error).__name__
                row.smtp_code = (
                    error.smtp_code if isinstance(error, SMTPResponseException) else None
                )
                row.attempts += 1
                row.last_attempt_at = timezone.now()
                # Only a refused recipient is about this message; login, sender, DATA
                # and connection 5xx describe the shop's account and may clear later.
                permanent = (
                    isinstance(error, SMTPRecipientsRefused)
                    and bool(error.recipients)
                    and all(500 <= response[0] < 600 for response in error.recipients.values())
                )
                failed = row.attempts == 6 or (
                    row.template_name != "purchase_confirmation" and permanent
                )
                row.status = "failed" if failed else "pending"
                row.next_attempt_at = (
                    row.last_attempt_at + timedelta(seconds=_DELAYS[row.attempts - 1])
                    if not failed
                    else None
                )
            else:
                row.attempts += 1
                row.last_attempt_at = row.sent_at = timezone.now()
                row.status = "sent"
                row.next_attempt_at = None
                row.last_error_class = ""
                row.smtp_code = None
                sent += 1
            row.save()


def send_due_telegram() -> int:
    if not settings.TELEGRAM_BOT_TOKEN:
        return 0
    sent = 0
    while True:
        with transaction.atomic():
            row = (
                Notification.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("order")
                .filter(channel="telegram", status="pending", next_attempt_at__lte=timezone.now())
                .order_by("next_attempt_at", "pk")
                .first()
            )
            if row is None:
                return sent
            # The customer row lock order services already take: an unlink or relink waits
            # for this send, so no message reaches a chat unlinked between check and send.
            current = (
                User.objects.select_for_update(no_key=True)
                .filter(pk=row.order.user_id)
                .values_list("telegram_id", flat=True)
                .first()
            )
            if row.template_name == Notification.Template.DELAY and _stale_delay(row):
                _drop(row)
                row.save()
                continue
            row.attempts += 1
            row.last_attempt_at = timezone.now()
            if current is None or current != row.chat_id:
                row.status, row.next_attempt_at = "failed", None
                row.last_error_class = "binding_changed"
            else:
                try:
                    telegram.send_message(
                        current, row.text_body, buttons=_order_button(row.order), rich=True
                    )
                except telegram.TelegramError as error:
                    row.last_error_class = f"TelegramError {error.status or 'network'}"
                    # 400 and 403 are about this chat or message (bot blocked, chat gone);
                    # 401, 5xx and timeouts are the shop's side and may clear.
                    failed = row.attempts == 6 or error.status in (400, 403)
                    row.status = "failed" if failed else "pending"
                    row.next_attempt_at = (
                        None
                        if failed
                        else row.last_attempt_at
                        + timedelta(seconds=max(_DELAYS[row.attempts - 1], error.retry_after or 0))
                    )
                else:
                    row.status, row.sent_at, row.next_attempt_at = "sent", row.last_attempt_at, None
                    row.last_error_class = ""
                    sent += 1
            row.save()

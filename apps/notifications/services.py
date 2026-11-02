import logging
import re
from datetime import timedelta
from smtplib import SMTPException, SMTPResponseException
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Product
from apps.core.email import render_email, send_templated_email
from apps.core.email_log import MailQuotaExceeded
from apps.notifications.models import ConfirmationSweep, Notification
from apps.orders.models import Order

logger = logging.getLogger(__name__)
_SITE_DESTINATION = re.compile(r"(\]\(\s*<?|^ {0,3}\[[^\]\n]+\]:\s*<?)/(?![/\\])", re.MULTILINE)
_DELAYS = (60, 300, 900, 3600, 21600)


def _absolute_links(source: str) -> str:
    return _SITE_DESTINATION.sub(
        lambda match: match[1] + settings.SITE_URL.rstrip("/") + "/", source
    )


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
                key: _absolute_links(facts.get(key, ""))
                for key in ("full_description", "usage_notes", "refund_note", "delivery_note")
            }
        )
        saved["specs"] = [
            {"title": spec["title"], "value": spec["value"]} for spec in facts.get("specs", [])
        ]
        saved["sections"] = [
            {"title": section.get("title", ""), "body": _absolute_links(section["body"])}
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
            {"title": page["title"], "body": _absolute_links(page["body"])}
            for page in order.policy_snapshot.values()
        ],
        "subtotal": order.subtotal,
        "discount_amount": order.discount_amount,
        "total_amount": order.total_amount,
    }


def render_confirmation(order: Order) -> tuple[str, str, str]:
    return render_email("purchase_confirmation", confirmation_context(order))


def discover_confirmations() -> int:
    existing = Notification.objects.filter(order_id=OuterRef("pk"))
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


def send_due_confirmations() -> int:
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
                .filter(status="pending", next_attempt_at__lte=now)
                .order_by("next_attempt_at", "pk")
                .first()
            )
            if row is None:
                return sent
            try:
                accepted = send_templated_email(
                    "purchase_confirmation",
                    to=[row.recipient],
                    context={},
                    rendered_parts=(row.subject, row.text_body, row.html_body),
                )
                if not accepted:
                    raise SMTPException("Confirmation email was not accepted")
            except MailQuotaExceeded:
                wait = timezone.now() + timedelta(minutes=15)
                sweep.quota_wait_until = wait
                sweep.save(update_fields=["quota_wait_until"])
                Notification.objects.filter(
                    status="pending", next_attempt_at__lte=timezone.now()
                ).update(next_attempt_at=wait)
                return sent
            except (
                Exception
            ) as error:  # noqa: BLE001 — uncertain and permanent failures share the ladder
                row.last_error_class = type(error).__name__
                row.smtp_code = (
                    error.smtp_code if isinstance(error, SMTPResponseException) else None
                )
                row.attempts += 1
                row.last_attempt_at = timezone.now()
                row.status = "failed" if row.attempts == 6 else "pending"
                row.next_attempt_at = (
                    row.last_attempt_at + timedelta(seconds=_DELAYS[row.attempts - 1])
                    if row.attempts < 6
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

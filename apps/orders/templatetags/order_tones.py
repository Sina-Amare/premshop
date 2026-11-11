"""The five status tones for the panel's own templates, from the same state module."""

from django import template

from apps.orders import status as order_status
from apps.payments.models import Payment

register = template.Library()

PAYMENT_TONES: dict[str, str] = {
    Payment.Status.CREATED: "progress",
    Payment.Status.INITIATED: "progress",
    Payment.Status.VERIFIED: "done",
    Payment.Status.FAILED: "problem",
    Payment.Status.ABANDONED: "closed",
}


register.filter("item_tone", order_status.item_tone)


@register.filter
def payment_tone(payment: Payment) -> str:
    return PAYMENT_TONES.get(payment.status, "closed")

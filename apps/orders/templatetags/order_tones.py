"""The five status tones for the panel's templates and the order emails, from the state module."""

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
def state_label(state: str) -> str:
    return order_status.STATES[state][0]


@register.filter
def email_pill(state: str) -> dict[str, str]:
    label, tone = order_status.STATES[state]
    background, color, border = order_status.EMAIL_TONES[tone]
    return {"label": label, "background": background, "color": color, "border": border}


@register.filter
def payment_tone(payment: Payment) -> str:
    return PAYMENT_TONES.get(payment.status, "closed")

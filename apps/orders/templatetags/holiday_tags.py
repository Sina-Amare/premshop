"""The open holiday, for the banner every panel page carries."""

from django import template

from apps.orders import holiday
from apps.orders.models import Holiday

register = template.Library()


@register.simple_tag
def open_holiday() -> Holiday | None:
    return holiday.current()

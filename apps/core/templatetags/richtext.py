"""`{{ text|markdown }}`: operator-written Markdown on a page (ADR-0027)."""

from __future__ import annotations

from django import template
from django.utils.safestring import SafeString

from apps.core.richtext import render_markdown

register = template.Library()


@register.filter(name="markdown")
def markdown(value: str | None, heading_levels: str | int = "3") -> SafeString:
    """`heading_levels` holds one digit per level the page leaves free: "23", or "" for none."""
    return render_markdown(value or "", [int(n) for n in str(heading_levels)])

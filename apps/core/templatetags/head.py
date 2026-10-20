"""What base.html prints twice in <head>: once for the browser, once for a link preview."""

from __future__ import annotations

import unicodedata
from html import unescape

from django import template
from django.template.base import NodeList, Parser, Token
from django.template.context import Context

register = template.Library()

SHOP = "PremShop"


def _ends_left_to_right(text: str) -> bool:
    """Whether the last strongly directional character is left-to-right.

    Digits, Persian ones included, and punctuation take the direction of the
    letter before them, so they are passed over.
    """
    for char in reversed(text):
        kind = unicodedata.bidirectional(char)
        if kind in ("R", "AL"):
            return False
        if kind == "L":
            return True
    return False


@register.filter(is_safe=True)
def shop_title(part: str) -> str:
    """The page's own title, then « | PremShop».

    A right-to-left mark follows a Latin run that ends the page's part: without
    it the bar and the brand join that run, and the tab reads «PremShop» before
    the name it should follow.
    """
    part = part.strip()
    if not part:
        return SHOP
    mark = "\N{RIGHT-TO-LEFT MARK}" if _ends_left_to_right(unescape(part)) else ""
    return f"{part}{mark} | {SHOP}"


class CaptureNode(template.Node):
    def __init__(self, nodelist: NodeList, name: str) -> None:
        self.nodelist, self.name = nodelist, name

    def render(self, context: Context) -> str:
        context[self.name] = self.nodelist.render(context)
        return ""


@register.tag
def capture(parser: Parser, token: Token) -> CaptureNode:
    """`{% capture name %}…{% endcapture %}` renders once into `name` and prints nothing.

    A block can appear once in a template, and the title and the description are
    each printed twice.
    """
    try:
        _, name = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError("{% capture %} takes one name") from None
    nodelist = parser.parse(("endcapture",))
    parser.delete_first_token()
    return CaptureNode(nodelist, name)

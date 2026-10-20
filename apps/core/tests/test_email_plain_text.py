"""The plain-text half of an email is not HTML, so nothing in it is escaped.

Rendered with the HTML defaults, a support address with an apostrophe arrived as
`o&#x27;brien`, and a link with a query string as `?a=1&amp;b=2`, which no longer
works once copied out of a text-only client. The HTML half keeps its escaping.
"""

from __future__ import annotations

import pytest
from django.core import mail

from apps.core.email import render_email, send_templated_email
from apps.core.views import EMAIL_PREVIEWS

ADDRESS = "o'brien&co@example.com"
LINK = "https://premshop.ir/d/x?a=1&b=2"
PRODUCT = "Tom's AT&T <Plus>"


@pytest.mark.parametrize("name", sorted(EMAIL_PREVIEWS))
def test_the_text_part_prints_the_support_address_as_typed(settings, name):
    settings.SUPPORT_EMAIL = ADDRESS

    _, text, html = render_email(name, EMAIL_PREVIEWS[name])

    assert ADDRESS in text
    assert "o&#x27;brien&amp;co@example.com" in html


def test_the_delivery_message_keeps_its_link_name_and_subject_intact():
    context = {
        **EMAIL_PREVIEWS["item_delivered"],
        "order_number": "PS-1&2",
        "product_name": PRODUCT,
        "delivery_url": LINK,
    }

    send_templated_email("item_delivered", to=["customer@example.test"], context=context)

    message = mail.outbox[0]
    assert message.subject == "سفارش PS-1&2 آماده است"
    assert PRODUCT in message.body
    assert LINK in message.body
    html = message.alternatives[0][0]
    assert "Tom&#x27;s AT&amp;T &lt;Plus&gt;" in html
    assert PRODUCT not in html
    assert 'href="https://premshop.ir/d/x?a=1&amp;b=2"' in html

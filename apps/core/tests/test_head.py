"""The page title: the page's own part, then the shop's name once, after a bar."""

from __future__ import annotations

import pytest
from django.template import Context, Template

from apps.core.templatetags.head import shop_title

RLM = "\N{RIGHT-TO-LEFT MARK}"


@pytest.mark.parametrize(
    "part, expected",
    [
        ("", "PremShop"),
        ("  ", "PremShop"),
        ("پرسش‌های متداول", "پرسش‌های متداول | PremShop"),
        (
            "خرید اشتراک ChatGPT Plus (چت جی‌پی‌تی پلاس)",
            "خرید اشتراک ChatGPT Plus (چت جی‌پی‌تی پلاس) | PremShop",
        ),
        # A Latin run at the end would take the bar and the brand into itself.
        ("Claude Pro", f"Claude Pro{RLM} | PremShop"),
        ("اشتراک Claude Pro", f"اشتراک Claude Pro{RLM} | PremShop"),
        ("جستجو: xbox", f"جستجو: xbox{RLM} | PremShop"),
        # Digits, Persian ones too, follow the letter before them.
        ("Office ۳۶۵", f"Office ۳۶۵{RLM} | PremShop"),
        ("اشتراک ۱۲ ماهه", "اشتراک ۱۲ ماهه | PremShop"),
        ("Gemini (جمنای)", "Gemini (جمنای) | PremShop"),
    ],
)
def test_the_shop_name_follows_the_page_once(part, expected):
    assert shop_title(part) == expected


def test_an_escaped_title_is_judged_by_what_it_shows():
    """`&amp;` ends in Latin letters to a regex and in «&» on screen."""
    rendered = Template(
        "{% load head %}{% capture t %}{{ name }}{% endcapture %}{{ t|shop_title }}"
    ).render(Context({"name": "ساعت <۱۲> & ۳۰"}))

    assert rendered == "ساعت &lt;۱۲&gt; &amp; ۳۰ | PremShop"


def test_a_captured_block_prints_nothing_and_is_escaped_once():
    template = Template(
        "{% load head %}[{% capture t %}{{ q }}{% endcapture %}]<title>{{ t|shop_title }}</title>"
        '<meta content="{{ t|shop_title }}">'
    )

    rendered = template.render(Context({"q": 'xbox "<b>'}))

    assert rendered == (
        f"[]<title>xbox &quot;&lt;b&gt;{RLM} | PremShop</title>"
        f'<meta content="xbox &quot;&lt;b&gt;{RLM} | PremShop">'
    )

from copy import deepcopy

import pytest

from apps.core.email import render_email
from apps.core.richtext import absolute_markdown_links, render_markdown
from apps.core.views import EMAIL_PREVIEWS


@pytest.mark.parametrize(
    "destination",
    [
        "/contact/",
        r"\/contact/",
        "&#47;contact/",
        "&sol;contact/",
        "&#x2F;contact/",
        "</contact/>",
        "/contact/(x)",
    ],
)
def test_decoded_site_links_are_absolute_in_both_email_parts(settings, destination):
    settings.SITE_URL = "https://shop.example.test"
    source = f"[contact]({destination})"
    rewritten = absolute_markdown_links(source)
    assert 'href="https://shop.example.test/contact/' in render_markdown(rewritten)
    data = deepcopy(EMAIL_PREVIEWS["purchase_confirmation"])
    data["lines"][0]["facts"]["full_description"] = rewritten
    data["policies"][0]["body"] = rewritten
    _, text, html = render_email("purchase_confirmation", data)
    assert text.count("https://shop.example.test/contact/") == 2
    assert html.count('href="https://shop.example.test/contact/') == 2
    assert source == f"[contact]({destination})"


@pytest.mark.parametrize(
    "destination", ["/contact/", r"\/contact/", "&#47;contact/", "&sol;contact/", "</contact/>"]
)
def test_reference_destinations_use_the_same_decoding(settings, destination):
    settings.SITE_URL = "https://shop.example.test"
    result = absolute_markdown_links(f"[contact][x]\n\n[x]: {destination}")
    assert 'href="https://shop.example.test/contact/"' in render_markdown(result)


@pytest.mark.parametrize(
    "source", ["[contact][x]\n\n[x]:\n /contact/", "[contact][a\\]b]\n\n[a\\]b]: /contact/"]
)
def test_multiline_and_escaped_reference_labels_keep_absolute_destinations(settings, source):
    settings.SITE_URL = "https://shop.example.test"
    result = absolute_markdown_links(source)
    assert 'href="https://shop.example.test/contact/"' in render_markdown(result)


def test_angle_destinations_preserve_unbalanced_parentheses(settings):
    settings.SITE_URL = "https://shop.example.test"
    assert 'href="https://shop.example.test/contact/("' in render_markdown(
        absolute_markdown_links("[contact](</contact/(>)")
    )


@pytest.mark.parametrize(
    "destination",
    [
        "//outside.test/",
        r"/\outside.test/",
        "&#47;&#47;outside.test/",
        "javascript:alert(1)",
        "mailto:a@example.test",
        "https://outside.test/",
    ],
)
def test_rewrite_does_not_promote_unsafe_or_external_destinations(destination):
    source = f"[link]({destination})"
    assert absolute_markdown_links(source) == source

"""The one renderer for operator-written text (ADR-0027).

Every payload goes through `render_markdown`. The raw-HTML payloads also go
straight into the sanitizer, in the shape they would have if the parser ever
let markup through, so the second layer is shown to hold on its own.
"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser

import pytest
from django.template import Context, Template
from django.utils.html import escape
from django.utils.safestring import SafeData

from apps.core import richtext
from apps.core.richtext import EXTERNAL_REL, isolate, isolate_latin, render_markdown

TAGS = set("p br strong a ul ol li table thead tbody tr th td bdi h3".split())
ATTRIBUTES = {"a": {"href", "rel"}, "ol": {"start"}, "bdi": {"lang"}}
# What a browser may follow from here: http, https, mailto, or a path on this site.
SAFE_HREF = re.compile(r"(?i)(https?|mailto):|/(?![/\\])")


class _StartTags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.seen.append((tag, attrs))


# A browser reads «<!-->» and «<!--->» as whole, empty comments and renders what
# follows them. html.parser on some Python versions, 3.12.3 on the CI runner
# among them, opens a comment there instead, and the check went blind to it.
ABRUPT_COMMENT = re.compile(r"<!--(?:->|>)")


def live_markup(html: str) -> list[str]:
    """Each tag, attribute or link in `html` that is outside what the renderer may emit."""
    parser = _StartTags()
    parser.feed(ABRUPT_COMMENT.sub("<!---->", html))
    parser.close()
    found = []
    for tag, attrs in parser.seen:
        if tag not in TAGS:
            found.append(f"<{tag}>")
        for name, value in attrs:
            value = value or ""
            if name not in ATTRIBUTES.get(tag, set()):
                found.append(f"{name} on <{tag}>")
            elif name == "rel" and value != EXTERNAL_REL:
                found.append(f"rel={value!r}")
            elif name == "lang" and value != "en":
                found.append(f"lang={value!r}")
            elif name == "href" and (
                not SAFE_HREF.match(value) or re.search(r"[\x00-\x20]", value)
            ):
                found.append(f"href={value!r}")
    return found


RAW_HTML = [
    "<script>alert(1)</script>",
    "<div>\n<script>alert(1)</script>\n</div>",
    'متن <b onmouseover="alert(1)">پررنگ</b> ادامه',
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<style>body{display:none}</style>",
    "<img src=x onerror=alert(1)>",
    '<p onclick="alert(1)">click</p>',
    '<a href="/ok" onmouseover="alert(1)">hover</a>',
    '<a href="javascript:alert(1)">x</a>',
    '<a href="JaVaScRiPt:alert(1)">x</a>',
    '<a href="&#106;avascript:alert(1)">x</a>',
    '<a href="&#x6A;&#x61;&#x76;&#x61;&#x73;&#x63;&#x72;&#x69;&#x70;&#x74;&#x3A;alert(1)">x</a>',
    '<a href="java&#x09;script:alert(1)">x</a>',
    '<a href=" javascript:alert(1)">x</a>',
    '<a href="vbscript:msgbox(1)">x</a>',
    '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
    '<a href="//evil.example">x</a>',
    '<a href="/\\evil.example">x</a>',
    '<a href="/\t/evil.example">x</a>',
    '<a href="https://ok.example" target="_blank" rel="opener">x</a>',
    '<bdi lang="fa" dir="ltr">x</bdi>',
    "<svg onload=alert(1)>",
    "<svg><script>alert(1)</script></svg>",
    '<svg><a xlink:href="javascript:alert(1)"><text>x</text></a></svg>',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<iframe srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;"></iframe>',
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
    "<!--><img src=x onerror=alert(1)>-->",
    '<object data="javascript:alert(1)"></object>',
    '<embed src="javascript:alert(1)">',
    '<form action="javascript:alert(1)"><button>x</button></form>',
    '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
    '<base href="//evil.example/">',
    '<div style="background:url(javascript:alert(1))">x</div>',
    "<h1>a second page title</h1>",
    '<p><em>x</em><img src="x"></p>',
]

MARKDOWN = [
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](&#106;avascript:alert(1))",
    "[x](javascript&#58;alert(1))",
    "[x](java&#x09;script:alert(1))",
    "[x](<javascript:alert(1)>)",
    "[x](< javascript:alert(1)>)",
    "[x](vbscript:msgbox(1))",
    "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "[x](data:image/png;base64,iVBORw0KGgo=)",
    "[x](//evil.example)",
    "[x](/\\evil.example)",
    "[x](\\\\evil.example)",
    '[x](https://ok.example "a title")',
    '[x](https://ok.example/"onmouseover="alert(1))',
    "<javascript:alert(1)>",
    "<JaVaScRiPt:alert(1)>",
    "<vbscript:msgbox(1)>",
    "<data:text/html;base64,PHNjcmlwdD4=>",
    "[x][r]\n\n[r]: javascript:alert(1)",
    "[x][r]\n\n[r]: //evil.example",
    "![x](javascript:alert(1))",
    "![x](https://evil.example/pixel.png)",
    "![x](data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+)",
    "[<img src=x onerror=alert(1)>](https://ok.example)",
    "[x](https://ok.example)[y](javascript:alert(1))",
    "[[x](javascript:alert(1))](https://ok.example)",
    "**<script>**alert(1)**</script>**",
    "*<img src=x onerror=alert(1)>*",
    "***[x](javascript:alert(1))***",
    "**[x](javascript:alert(1)**)",
    "| <script>alert(1)</script> | [x](javascript:alert(1)) |\n|---|---|\n| <img src=x> | b |",
    "| a |\n|:-:|\n| b |",
    "# <script>alert(1)</script>",
    "- [x](javascript:alert(1))\n  - <svg onload=alert(1)>",
    "\\<script>alert(1)\\</script>",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "javascript:alert(1)",
    "```html\n<script>alert(1)</script>\n```",
    "    <script>alert(1)</script>",
    "`<script>alert(1)</script>`",
    "> <script>alert(1)</script>",
]


@pytest.mark.parametrize("payload", RAW_HTML + MARKDOWN)
def test_every_payload_renders_inert(payload):
    assert live_markup(render_markdown(payload)) == []


@pytest.mark.parametrize("payload", RAW_HTML)
def test_the_sanitizer_alone_makes_raw_html_inert(payload):
    assert live_markup(richtext._sanitize(payload, {"h3"})) == []


def test_the_sanitizer_reads_a_long_href_in_linear_time():
    """Its own URL check once took quadratic time over the spaces inside a link."""
    started = time.perf_counter()
    richtext._sanitize('<a href="/' + " " * 40_000 + 'x">x</a>', set())
    assert time.perf_counter() - started < 1


@pytest.mark.parametrize("payload", RAW_HTML)
def test_the_check_sees_each_raw_payload_as_live(payload):
    """The control for the two tests above: a check that saw nothing would pass them."""
    assert live_markup(payload)


def test_the_result_is_marked_safe_for_templates():
    assert isinstance(render_markdown("متن"), SafeData)


def test_paragraphs_and_line_breaks():
    assert render_markdown("خط یک\nخط دو\n\nبند دوم") == (
        "<p>خط یک<br>\nخط دو</p>\n<p>بند دوم</p>\n"
    )


def test_bulleted_and_numbered_lists():
    assert render_markdown("- یک\n- دو") == "<ul>\n<li>یک</li>\n<li>دو</li>\n</ul>\n"
    assert render_markdown("1. یک\n2. دو") == "<ol>\n<li>یک</li>\n<li>دو</li>\n</ol>\n"


def test_a_list_numbered_with_persian_digits_is_a_list():
    assert render_markdown("۱. یک\n۲. دو") == render_markdown("1. یک\n2. دو")
    assert render_markdown("۳. سه\n۴. چهار").startswith('<ol start="3">')


def test_text_from_a_browser_textarea_reads_the_same():
    """A textarea submits CRLF line endings."""
    assert render_markdown("۱. یک\r\n۲. دو") == render_markdown("1. یک\n2. دو")
    assert render_markdown("خط یک\r\nخط دو") == "<p>خط یک<br>\nخط دو</p>\n"


def test_bold_renders_and_single_emphasis_reads_as_plain_text():
    assert render_markdown("**مهم** و *کج* و _زیر_") == ("<p><strong>مهم</strong> و کج و زیر</p>\n")


def test_a_path_on_this_site_is_an_internal_link():
    assert render_markdown("[سبد خرید](/cart/)") == '<p><a href="/cart/">سبد خرید</a></p>\n'


@pytest.mark.parametrize(
    "url", ["https://help.openai.com/", "http://example.com/a", "mailto:support@premshop.ir"]
)
def test_any_other_link_is_external_and_opens_in_place(url):
    assert render_markdown(f"[پیوند]({url})") == (
        f'<p><a href="{url}" rel="{EXTERNAL_REL}">پیوند</a></p>\n'
    )


@pytest.mark.parametrize(
    "url", ["https://premshop.ir/p/chatgpt-plus/", "http://premshop.ir/faq/", "https://premshop.ir"]
)
def test_a_full_address_on_the_shops_own_host_is_an_internal_link(settings, url):
    """Guides link to products by full address, and a product page is not a
    stranger's site to be marked nofollow."""
    settings.SITE_URL = "https://premshop.ir"

    assert render_markdown(f"[پیوند]({url})") == f'<p><a href="{url}">پیوند</a></p>\n'


@pytest.mark.parametrize(
    "url",
    [
        "https://premshop.ir.example.com/",
        "https://premshop.ir@example.com/",
        "https://example.com/premshop.ir",
        "https://www.premshop.ir/",
        "mailto:support@premshop.ir",
    ],
)
def test_an_address_that_only_resembles_the_shops_host_stays_external(settings, url):
    settings.SITE_URL = "https://premshop.ir"

    assert f'rel="{EXTERNAL_REL}"' in render_markdown(f"[پیوند]({url})")


@pytest.mark.parametrize(
    "source, expected",
    [
        ("### تیتر", "<h3>تیتر</h3>\n"),
        ("## تیتر", "<h3>تیتر</h3>\n"),
        ("# تیتر", "<h3>تیتر</h3>\n"),
        ("#### تیتر", "<p>تیتر</p>\n"),
        ("تیتر\n===", "<p>تیتر<br>\n===</p>\n"),
    ],
)
def test_headings_default_to_h3_alone(source, expected):
    assert render_markdown(source) == expected


def test_a_page_with_no_free_heading_level_gets_paragraphs():
    assert render_markdown("# یک\n### سه", heading_levels=()) == "<p>یک</p>\n<p>سه</p>\n"


def test_a_page_can_leave_two_levels_free():
    assert render_markdown("# یک\n### سه\n#### چهار", heading_levels=(2, 3)) == (
        "<h2>یک</h2>\n<h3>سه</h3>\n<p>چهار</p>\n"
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        ("### سه\n#### چهار", "<h2>سه</h2>\n<h3>چهار</h3>\n"),
        ("### سه\n## دو", "<h2>سه</h2>\n<h2>دو</h2>\n"),
        ("#### چهار", "<p>چهار</p>\n"),
    ],
)
def test_a_text_that_opens_below_the_first_free_level_moves_up(source, expected):
    """Under no title of its own, «###» as the help teaches it opened the text
    with an h3 straight after the page's h1."""
    assert render_markdown(source, heading_levels=(2, 3)) == expected


@pytest.mark.parametrize("levels", [(1,), (1, 3), (7,)])
def test_h1_is_never_on_offer(levels):
    with pytest.raises(ValueError):
        render_markdown("# تیتر", heading_levels=levels)


def test_tables_keep_their_header_row_and_lose_inline_style():
    assert render_markdown("| الف | ب |\n|:-:|--:|\n| ۱ | ۲ |") == (
        "<table>\n<thead>\n<tr>\n<th>الف</th>\n<th>ب</th>\n</tr>\n</thead>\n"
        "<tbody>\n<tr>\n<td>۱</td>\n<td>۲</td>\n</tr>\n</tbody>\n</table>\n"
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        ("> نقل", "<p>&gt; نقل</p>\n"),
        ("---", "<p>---</p>\n"),
        ("`کد`", "<p>`کد`</p>\n"),
        ("    تورفته", "<p>تورفته</p>\n"),
        (
            "![عکس](https://example.com/a.png)",
            f'<p>!<a href="https://example.com/a.png" rel="{EXTERNAL_REL}">عکس</a></p>\n',
        ),
    ],
)
def test_syntax_outside_the_subset_prints_as_typed(source, expected):
    assert render_markdown(source) == expected


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            "از جمله VS Code،\u200f Cursor",
            '<p>از جمله <bdi lang="en">VS Code</bdi>،\u200f <bdi lang="en">Cursor</bdi></p>\n',
        ),
        ("Google Photos. ۱۵ گیگابایت", '<p><bdi lang="en">Google Photos</bdi>. ۱۵ گیگابایت</p>\n'),
        ("پلن 12 Months Family Plan", '<p>پلن <bdi lang="en">12 Months Family Plan</bdi></p>\n'),
        (
            "C++، iCloud+ و GPT-4o.",
            '<p><bdi lang="en">C++</bdi>، <bdi lang="en">iCloud+</bdi> و <bdi lang="en">GPT-4o</bdi>.</p>\n',
        ),
    ],
)
def test_latin_runs_are_isolated_whole(source, expected):
    assert render_markdown(source) == expected


def test_persian_text_and_bare_numbers_are_left_alone():
    text = "۱۲ ماه، 12 ماه، می\u200cشود"
    assert render_markdown(text) == f"<p>{text}</p>\n"


def test_a_run_is_escaped_before_it_is_wrapped():
    assert render_markdown("AT&T و a<b") == (
        '<p><bdi lang="en">AT&amp;T</bdi> و <bdi lang="en">a</bdi>&lt;<bdi lang="en">b</bdi></p>\n'
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        ("**Chat**GPT", '<p><bdi lang="en"><strong>Chat</strong>GPT</bdi></p>\n'),
        ("ChatGPT **Plus**", '<p><bdi lang="en">ChatGPT <strong>Plus</strong></bdi></p>\n'),
        (
            "[Google One](https://one.google.com/) AI Premium",
            f'<p><bdi lang="en"><a href="https://one.google.com/" rel="{EXTERNAL_REL}">Google One</a>'
            " AI Premium</bdi></p>\n",
        ),
        (
            "ChatGPT **Plus ماهانه**",
            '<p><bdi lang="en">ChatGPT <strong>Plus</strong></bdi><strong> ماهانه</strong></p>\n',
        ),
        (
            "**ماهانه Chat**GPT",
            '<p><strong>ماهانه </strong><bdi lang="en"><strong>Chat</strong>GPT</bdi></p>\n',
        ),
        ("*Gem*ini", '<p><bdi lang="en">Gemini</bdi></p>\n'),
    ],
)
def test_a_run_split_by_bold_or_a_link_is_isolated_once(source, expected):
    """Two isolates side by side in a right-to-left line swap places: «Plus ChatGPT»."""
    assert render_markdown(source) == expected


READING_PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<style>p {{ white-space: nowrap }}</style></head><body>{}</body></html>"""

ON_SCREEN = """
(words) => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  let n = 0, at = 0;
  return words.map((word) => {
    for (; n < nodes.length; n++, at = 0) {
      const i = nodes[n].data.indexOf(word, at);
      if (i >= 0) {
        const range = document.createRange();
        range.setStart(nodes[n], i);
        range.setEnd(nodes[n], i + word.length);
        at = i + word.length;
        return range.getBoundingClientRect().left;
      }
    }
    return null;
  });
}
"""


@pytest.mark.browser
@pytest.mark.parametrize(
    "source, left_to_right",
    [
        ("از جمله VS Code،\u200f Cursor", ["Cursor", "VS", "Code"]),
        ("Google Photos. ۱۵ گیگابایت", ["۱۵", "Google", "Photos"]),
        ("پلن ChatGPT **Plus** را بخرید", ["ChatGPT", "Plus"]),
        ("پلن **Chat**GPT را بخرید", ["Chat", "GPT"]),
        (
            "سرویس [Google One](https://one.google.com/) AI Premium",
            ["Google", "One", "AI", "Premium"],
        ),
        ("پلن ChatGPT **Plus ماهانه** است", ["ChatGPT", "Plus"]),
    ],
)
def test_latin_names_keep_their_order_in_a_persian_line(browser, source, left_to_right):
    page = browser.new_page(viewport={"width": 390, "height": 300})
    try:
        page.set_content(READING_PAGE.format(render_markdown(source)))
        in_text_order = sorted(left_to_right, key=source.index)
        xs = page.evaluate(ON_SCREEN, in_text_order)
    finally:
        page.close()
    assert None not in xs
    assert [word for _, word in sorted(zip(xs, in_text_order, strict=True))] == left_to_right


PLAIN_LINES = [
    ("از جمله VS Code، Cursor", ["Cursor", "VS", "Code"]),
    ("Google Photos. ۱۵ گیگابایت", ["۱۵", "Google", "Photos"]),
]


def words_on_screen(browser, html: str, words: list[str]) -> list[str]:
    page = browser.new_page(viewport={"width": 390, "height": 300})
    try:
        page.set_content(READING_PAGE.format(f"<p>{html}</p>"))
        xs = page.evaluate(ON_SCREEN, words)
    finally:
        page.close()
    assert None not in xs
    return [word for _, word in sorted(zip(xs, words, strict=True))]


@pytest.mark.browser
@pytest.mark.parametrize("source, left_to_right", PLAIN_LINES)
def test_a_one_line_field_keeps_latin_names_in_order(browser, source, left_to_right):
    in_text_order = sorted(left_to_right, key=source.index)

    assert words_on_screen(browser, isolate_latin(source), in_text_order) == left_to_right


@pytest.mark.browser
@pytest.mark.parametrize("source, left_to_right", PLAIN_LINES)
def test_without_isolation_the_same_lines_read_out_of_order(browser, source, left_to_right):
    """The control: the order test above would pass on lines that need no help."""
    in_text_order = sorted(left_to_right, key=source.index)

    assert words_on_screen(browser, escape(source), in_text_order) != left_to_right


def test_a_one_line_field_is_escaped_and_reads_no_markdown():
    assert isolate_latin("**ChatGPT** و <script>x</script>") == (
        '**<bdi lang="en">ChatGPT</bdi>** و &lt;<bdi lang="en">script</bdi>&gt;<bdi lang="en">x</bdi>&lt;/<bdi lang="en">script</bdi>&gt;'
    )
    assert isinstance(isolate_latin("x"), SafeData)
    assert isolate_latin("فقط فارسی، ۱۲ ماهه") == "فقط فارسی، ۱۲ ماهه"


def test_a_whole_value_is_one_isolate_marked_english_only_when_it_is():
    """Names, titles and a search query stay one isolate; `lang` tells a screen
    reader which voice to use, so it goes only on Latin."""
    assert isolate("Claude Pro") == '<bdi lang="en">Claude Pro</bdi>'
    assert isolate("اشتراک Netflix") == '<bdi>اشتراک <bdi lang="en">Netflix</bdi></bdi>'
    assert isolate("۱ ماهه") == "<bdi>۱ ماهه</bdi>"
    assert "<b>" not in isolate("<b>x</b>")
    assert isinstance(isolate("x"), SafeData)


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            "[<https://x.example>Plus](/a)",
            '<p><a href="/a"><bdi lang="en">https://x.examplePlus</bdi></a></p>\n',
        ),
        (
            "[x <https://x.example> y](/a)",
            '<p><a href="/a"><bdi lang="en">x https://x.example y</bdi></a></p>\n',
        ),
    ],
)
def test_an_autolink_inside_link_text_does_not_nest_links(source, expected):
    """HTML ends the outer <a> where an inner one opens, leaving an empty link behind."""
    assert render_markdown(source) == expected


def test_link_text_is_isolated_and_the_href_is_not():
    assert render_markdown("[ChatGPT Plus](https://openai.com/chatgpt/)") == (
        f'<p><a href="https://openai.com/chatgpt/" rel="{EXTERNAL_REL}">'
        '<bdi lang="en">ChatGPT Plus</bdi></a></p>\n'
    )


def test_headings_and_table_cells_isolate_too():
    assert render_markdown("### Google AI Pro") == '<h3><bdi lang="en">Google AI Pro</bdi></h3>\n'
    assert '<td><bdi lang="en">Gemini</bdi></td>' in render_markdown("| مدل |\n|---|\n| Gemini |")


@pytest.mark.parametrize("source", ["", "  \n\n"])
def test_empty_text_renders_nothing(source):
    assert render_markdown(source) == ""


def render(template: str, value: str | None) -> str:
    return Template("{% load richtext %}" + template).render(Context({"value": value}))


@pytest.mark.parametrize("value", [None, ""])
def test_the_filter_prints_nothing_for_no_text(value):
    assert render("{{ value|markdown }}", value) == ""


def test_the_filter_output_is_not_escaped_twice():
    assert render("{{ value|markdown }}", "**مهم**") == "<p><strong>مهم</strong></p>\n"


@pytest.mark.parametrize(
    "template, expected",
    [
        ("{{ value|markdown }}", "<h3>تیتر</h3>\n"),
        ('{{ value|markdown:"" }}', "<p>تیتر</p>\n"),
        ('{{ value|markdown:"23" }}', "<h2>تیتر</h2>\n"),
        ("{{ value|markdown:3 }}", "<h3>تیتر</h3>\n"),
    ],
)
def test_the_filter_takes_the_free_heading_levels(template, expected):
    assert render(template, "# تیتر") == expected

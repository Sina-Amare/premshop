"""Operator-written text as HTML: restricted Markdown through one function (ADR-0027).

Product sections, category intros and guides all reach the page through
`render_markdown`, so one place decides what an operator's text may become on a
page that takes payment. Two layers: markdown-it-py parses with raw HTML off, so a
tag the operator types is printed as text; nh3 then removes anything outside the
allowlist, so a parser mistake does not reach the page either.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Sequence
from itertools import pairwise

import nh3
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

from apps.core.formatting import to_latin_digits

EXTERNAL_REL = "nofollow noopener noreferrer"
_SCHEMES = {"http", "https", "mailto"}
_TAGS = set("p br strong a ul ol li table thead tbody tr th td bdi".split())

_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
# A path on this site. To a browser, "//host" and "/\host" name another site.
_SITE_PATH = re.compile(r"/(?![/\\])")
# What a browser removes from a URL before reading it, so "/<tab>/host" is "//host".
_C0_AND_SPACE = "".join(map(chr, range(0x21)))
_TAB_AND_NEWLINE = dict.fromkeys(map(ord, "\t\n\r"))

_LATIN = "A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"
# Words, digits and the ASCII punctuation between them, ending on a letter, a digit
# or a mark that belongs to a name (C++, C#, 100%, a URL's last slash), so the full
# stop of a Persian sentence stays outside. Brackets are excluded: a run holding one
# half of a pair would leave the other half to be mirrored the wrong way.
_LATIN_RUN = re.compile(
    rf"([{_LATIN}0-9](?:[{_LATIN}0-9 !\"#$%&'*+,\-./:;=?@\\^_`|~]*[{_LATIN}0-9+#%/])?)"
)
_LATIN_LETTER = re.compile(f"[{_LATIN}]")
# A run passes through the edges of bold and links: `**Chat**GPT` is one name.
_CROSSABLE = {"strong_open", "strong_close", "link_open", "link_close"}
# CommonMark reads only ASCII digits as list numbers, and a Persian keyboard types "۱.".
_PERSIAN_LIST_NUMBER = re.compile(r"^([ \t]*)([۰-۹]{1,9})(?=[.)](?:[ \t]|$))", re.MULTILINE)


def _isolated(children: list[Token]) -> list[Token]:
    """`children` with each Latin run inside one <bdi>, across bold and link edges.

    Two isolates side by side in a right-to-left line swap places, so a <bdi> per text
    token would show `ChatGPT **Plus**` as «Plus ChatGPT».
    """
    shown = [_shown(token) for token in children]
    runs = [m.span() for m in _LATIN_RUN.finditer("".join(shown)) if _LATIN_LETTER.search(m[0])]
    if not runs:
        return children
    # Cells are tokens with text tokens cut at the run edges, so each run is a row of
    # whole cells; `starting` and `ending` find a cell by where its text starts or ends.
    edges = sorted({edge for run in runs for edge in run})
    cells: list[tuple[Token, str]] = []
    starting: dict[int, int] = {}
    ending: dict[int, int] = {}
    at = k = 0
    for token, text in zip(children, shown, strict=True):
        cuts = []
        while k < len(edges) and edges[k] < at + len(text):
            if edges[k] > at:
                cuts.append(edges[k] - at)
            k += 1
        for i, j in pairwise([0, *cuts, len(text)]):
            piece = text[i:j]
            if piece:
                starting[at] = ending[at + len(piece)] = len(cells)
            at += len(piece)
            cells.append((token if not cuts else Token("text", "", 0, content=piece), piece))
    partner: dict[int, int] = {}
    opened: list[int] = []
    for i, (token, _) in enumerate(cells):
        if token.type in _CROSSABLE:
            if token.nesting == 1:
                opened.append(i)
            else:
                partner[i] = opened.pop()
                partner[partner[i]] = i
    inserts: dict[int, list[Token]] = defaultdict(list)
    for start, end in runs:
        lo, hi = _take_in_tags(cells, partner, starting[start], ending[end] + 1)
        tags = [i for i in range(lo, hi) if i in partner]
        # Bold or a link that also holds Persian is split at the run's edge, as a browser
        # splits misnested formatting: one <bdi> cannot hold half an element.
        outer = [cells[partner[i]][0] for i in tags if partner[i] < lo]
        inner = [cells[i][0] for i in tags if partner[i] >= hi]
        inserts[lo] += [*map(_closing, outer), Token("bdi_open", "bdi", 1), *reversed(outer)]
        inserts[hi] += [*map(_closing, reversed(inner)), Token("bdi_close", "bdi", -1), *inner]
    out: list[Token] = []
    for i, (token, _) in enumerate(cells):
        out += inserts.get(i, [])
        out.append(token)
    return out + inserts.get(len(cells), [])


def _shown(token: Token) -> str:
    """The text a token puts on screen; a run passes through a bold or link tag."""
    if token.type == "text":
        return token.content
    if token.type in _CROSSABLE:
        return ""
    # *word* prints its asterisks; a line break is a character no run holds.
    return token.markup if token.type.startswith("em_") else "\n"


def _take_in_tags(
    cells: list[tuple[Token, str]], partner: dict[int, int], lo: int, hi: int
) -> tuple[int, int]:
    """Widen [lo, hi) over the tags just outside it whose partners are inside it."""
    while reach := [
        j
        for i in range(lo, hi)
        if (j := partner.get(i)) is not None
        and not lo <= j < hi
        and not any(ch for _, ch in cells[min(j, hi) : max(j, lo)])
    ]:
        lo, hi = min(lo, *reach), max(hi, *(j + 1 for j in reach))
    return lo, hi


def _closing(token: Token) -> Token:
    return Token(token.type.replace("_open", "_close"), token.tag, -1)


def _without_nested_links(children: list[Token]) -> list[Token]:
    # An autolink inside link text nests <a> in <a>, and HTML ends the outer link where
    # the inner one opens, leaving it empty. The outer link keeps all of the text.
    out, depth = [], 0
    for token in children:
        if token.type == "link_close":
            depth -= 1
        if depth == 0 or token.type not in ("link_open", "link_close"):
            out.append(token)
        if token.type == "link_open":
            depth += 1
    return out


def _link_open(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    token = tokens[idx]
    if not _SITE_PATH.match(str(token.attrGet("href") or "")):
        token.attrSet("rel", EXTERNAL_REL)
    return self.renderToken(tokens, idx, options, env)


def _as_written(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    # Persian is never set in italics, so *word* reads as plain text: no <em>,
    # and no stray asterisks in front of a customer.
    return ""


class _Parser(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        # An allowlist: the library's own check is a blocklist of four schemes.
        scheme = _SCHEME.match(url)
        return bool(_SITE_PATH.match(url) or (scheme and scheme[1].lower() in _SCHEMES))


# Each disabled rule would emit markup the allowlist does not hold (code, quotes,
# rules, images, the underlined h1/h2 form); without it the text prints as typed.
_parser = (
    _Parser("commonmark", {"html": False, "breaks": True})
    .enable("table")
    .disable(["code", "fence", "blockquote", "hr", "lheading", "backticks", "image"])
)
_parser.add_render_rule("link_open", _link_open)
_parser.add_render_rule("em_open", _as_written)
_parser.add_render_rule("em_close", _as_written)


def _site_path_or_none(url: str) -> str | None:
    return url if _SITE_PATH.match(url.strip(_C0_AND_SPACE).translate(_TAB_AND_NEWLINE)) else None


def _sanitize(html: str, headings: set[str]) -> str:
    return nh3.clean(
        html,
        tags=_TAGS | headings,
        # "*" replaces nh3's default lang and title on every tag.
        attributes={"*": set(), "a": {"href"}, "ol": {"start"}},
        tag_attribute_values={"a": {"rel": {EXTERNAL_REL}}},
        url_schemes=_SCHEMES,
        url_relative=_site_path_or_none,
        link_rel=None,
    )


def render_markdown(text: str, heading_levels: Collection[int] = (3,)) -> SafeString:
    """Operator-written Markdown as HTML that is safe on any page.

    `heading_levels` are the levels the calling page leaves free below its own
    headings. A heading above them moves down to the nearest one; a heading below
    them, or any heading where none is free, becomes a paragraph.
    """
    levels = sorted(set(heading_levels))
    if any(n not in range(2, 7) for n in levels):
        raise ValueError(f"heading levels run from 2 to 6, not {levels}: h1 is the page's title")
    source = _PERSIAN_LIST_NUMBER.sub(lambda m: m[1] + to_latin_digits(m[2]), text)
    tokens = _parser.parse(source)
    for token in tokens:
        if token.type in ("heading_open", "heading_close"):
            token.tag = next((f"h{n}" for n in levels if n >= int(token.tag[1])), "p")
        elif token.type == "inline" and token.children:
            token.children = _isolated(_without_nested_links(token.children))
    headings = {f"h{n}" for n in levels}
    html = _sanitize(_parser.renderer.render(tokens, _parser.options, {}), headings)
    # The allowlist has just seen every byte of it.
    return mark_safe(html)  # noqa: S308  # nosec B308, B703

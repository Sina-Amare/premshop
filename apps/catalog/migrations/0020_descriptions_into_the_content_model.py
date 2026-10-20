"""Today's descriptions and category introductions, moved into the content model.

A product's full description becomes a «متن آزاد» section wherever the page
printed it, which was wherever it differed from the short one. A category's
introduction becomes restricted Markdown in `intro`: paragraphs, bold, links and
lists carry over, every other tag is dropped, and script and style go with their
content. Neither touches the old column, so going back loses nothing.

The text is escaped so it prints as it did under `linebreaks`: a «*», a «#» or
«۱.» at the start of a line is the operator's character, not Markdown.
"""

import re
from html.parser import HTMLParser

from django.db import migrations

# The heading the page printed above the full description.
FREE_TITLE = "توضیحات"

# Markdown that plain text must not set off anywhere in a line: escapes,
# emphasis, links, autolinks, tables and character references.
INLINE = re.compile(r"([\\*_\[\]<|])")
REFERENCE = re.compile(r"&(?=#?\w+;)")
# And at the start of a line: a heading, a bullet, a numbered item.
LINE_MARK = re.compile(r"^([ \t]*)([#+-])", re.MULTILINE)
LINE_NUMBER = re.compile(r"^([ \t]*[0-9۰-۹]+)([.)])", re.MULTILINE)


def escape_inline(text):
    return REFERENCE.sub(r"\\&", INLINE.sub(r"\\\1", text))


def escape_line_starts(text):
    return LINE_NUMBER.sub(r"\1\\\2", LINE_MARK.sub(r"\1\\\2", text))


def as_literal_markdown(text):
    """Markdown that prints exactly `text`, its line breaks included."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return escape_line_starts(escape_inline(text))


def link_target(href):
    """`href` if the renderer would keep it, else None: the text stays, unlinked."""
    href = (href or "").strip()
    scheme = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", href)
    allowed = scheme and scheme.group(1).lower() in {"http", "https", "mailto"}
    if not (allowed or re.match(r"/(?![/\\])", href)) or re.search(r"[<>\s]", href):
        return None
    return f"<{href}>" if re.search(r"[()]", href) else href


class IntroToMarkdown(HTMLParser):
    BLOCKS = set("p div section article header footer blockquote pre table tr hr".split()) | {
        f"h{n}" for n in range(1, 7)
    }
    DROPPED = {"script", "style", "template", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []  # (markdown, the list it is an item of, or None)
        self.open = [["block", [], None]]  # inline elements still open, block at the bottom
        self.lists = []  # [tag, items so far]
        self.lists_begun = 0
        self.dropping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.DROPPED:
            self.dropping += 1
        elif self.dropping:
            return
        elif tag in ("strong", "b"):
            self.open.append([tag, [], None])
        elif tag == "a":
            self.open.append([tag, [], link_target(dict(attrs).get("href"))])
        elif tag == "br":
            self.open[-1][1].append("\n")
        elif tag in ("ul", "ol"):
            self.end_block()
            self.lists_begun += not self.lists
            self.lists.append([tag, 0])
        elif tag == "li":
            self.end_block()
            if self.lists:
                self.lists[-1][1] += 1
        elif tag in self.BLOCKS:
            self.end_block()

    def handle_endtag(self, tag):
        if tag in self.DROPPED:
            self.dropping = max(0, self.dropping - 1)
        elif self.dropping:
            return
        elif tag in ("strong", "b", "a"):
            if any(entry[0] == tag for entry in self.open[1:]):
                while self.close_inline() != tag:
                    pass
        elif tag in ("ul", "ol"):
            self.end_block()
            if self.lists:
                self.lists.pop()
        elif tag == "li" or tag in self.BLOCKS:
            self.end_block()

    def handle_data(self, data):
        if not self.dropping:
            self.open[-1][1].append(escape_inline(re.sub(r"\s+", " ", data)))

    def close_inline(self):
        tag, pieces, href = self.open.pop()
        text = "".join(pieces)
        core = text.strip()
        if core and tag in ("strong", "b"):
            core = f"**{core}**"
        elif core and tag == "a" and href:
            core = f"[{core}]({href})"
        # Spaces stay outside the marks, or `** bold **` would print its asterisks.
        lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]
        self.open[-1][1].append(lead + core + trail if core else text)
        return tag

    def end_block(self):
        while len(self.open) > 1:
            self.close_inline()
        text = "".join(self.open[0][1])
        lines = [re.sub(" {2,}", " ", line).strip() for line in text.split("\n")]
        self.open[0][1] = []
        text = escape_line_starts("\n".join(line for line in lines if line))
        if not text:
            return
        if self.lists:
            tag, count = self.lists[-1]
            mark = f"{count or 1}. " if tag == "ol" else "- "
            indent = "    " * (len(self.lists) - 1)
            text = indent + mark + text.replace("\n", "\n" + indent + " " * len(mark))
        self.blocks.append((text, self.lists_begun if self.lists else None))

    def markdown(self):
        self.end_block()
        out = ""
        for i, (text, in_list) in enumerate(self.blocks):
            if i:
                # Items of one list on consecutive lines, anything else apart.
                same = in_list is not None and in_list == self.blocks[i - 1][1]
                out += "\n" if same else "\n\n"
            out += text
        return out


def intro_as_markdown(html):
    if not re.search(r"<[A-Za-z/!]", html):
        # Plain text, which the page has printed through `linebreaks` since the
        # HTML stopped running: its line breaks are its paragraphs.
        return as_literal_markdown(html.strip())
    parser = IntroToMarkdown()
    parser.feed(html)
    parser.close()
    return parser.markdown()


def forwards(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Product = apps.get_model("catalog", "Product")
    ProductSection = apps.get_model("catalog", "ProductSection")
    for category in Category.objects.exclude(intro_html=""):
        category.intro = intro_as_markdown(category.intro_html)
        category.save(update_fields=["intro"])
    ProductSection.objects.bulk_create(
        ProductSection(
            product=product,
            kind="free",
            title=FREE_TITLE,
            body=as_literal_markdown(product.full_description),
        )
        for product in Product.objects.exclude(full_description="")
        if product.full_description != product.short_description
    )


def backwards(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Product = apps.get_model("catalog", "Product")
    ProductSection = apps.get_model("catalog", "ProductSection")
    Category.objects.update(intro="")
    # Only the sections this migration made, as it made them.
    for product in Product.objects.exclude(full_description=""):
        ProductSection.objects.filter(
            product=product,
            kind="free",
            title=FREE_TITLE,
            body=as_literal_markdown(product.full_description),
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0019_content_sections_and_facts"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

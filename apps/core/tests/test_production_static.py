"""The customer pages, and every file name written out, under production's static storage.

Manifest storage serves every file under a name that carries its content hash,
which is what lets nginx cache it for a year. The cost is that a `{% static %}`
naming a file collectstatic never saw raises instead of printing a dead link, so
a page that renders in development can be a 500 in production. The pages are
rendered; what no page here reaches is searched for.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

import config.settings.base as base
from apps.cart.models import Cart, CartItem
from apps.core.tests.test_site_url import PRODUCTION
from apps.orders.tests.conftest import buyer as buyer
from apps.orders.tests.conftest import encryption_keys as encryption_keys
from apps.orders.tests.conftest import plan as plan

pytestmark = pytest.mark.django_db

# Links name another page; every other URL in a tag is something the browser fetches.
NOT_LOADED = {"canonical", "alternate", "next", "prev", "author", "license"}
URL_ATTRIBUTES = {"src", "href", "srcset", "poster", "data"}
CSS_URL = re.compile(r"""(?:url\(\s*['"]?|@import\s+['"])([^'")\s]+)""")

ROOT = Path(settings.BASE_DIR)
# A file name written out in the tag; `{% static some_name %}` has no quote to match.
TEMPLATE_STATIC = re.compile(r"""\{%\s*static\s+(['"])(?P<path>[^'"]+)\1""")


def production_storages() -> dict:
    """STORAGES as prod.py sets it, read without leaving prod's environment behind."""
    original = dict(os.environ)
    os.environ.update(PRODUCTION, SITE_URL="https://premshop.ir")
    try:
        importlib.reload(base)
        sys.modules.pop("config.settings.prod", None)
        return importlib.import_module("config.settings.prod").STORAGES
    finally:
        os.environ.clear()
        os.environ.update(original)
        sys.modules.pop("config.settings.prod", None)
        importlib.reload(base)


@pytest.fixture(scope="module")
def collected(tmp_path_factory):
    root = tmp_path_factory.mktemp("staticfiles")
    with override_settings(STORAGES=production_storages(), STATIC_ROOT=root):
        # The Dockerfile's command: Tailwind's source is never collected.
        call_command("collectstatic", interactive=False, verbosity=0, ignore_patterns=["input.css"])
        yield root


def customer_pages(buyer, plan) -> dict[str, str]:
    visitor = Client()
    pages = {
        "home": visitor.get(reverse("home")),
        "category": visitor.get(reverse("category", args=[plan.product.category.slug])),
        "product": visitor.get(reverse("product", args=[plan.product.slug])),
        "search": visitor.get(reverse("search"), {"q": plan.product.name}),
        "sign-in": visitor.get(reverse("login")),
    }
    CartItem.objects.create(cart=Cart.objects.create(user=buyer), plan=plan)
    customer = Client()
    customer.force_login(buyer)
    pages["cart"] = customer.get(reverse("cart"))
    pages["checkout"] = customer.get(reverse("checkout"))
    for name, response in pages.items():
        assert response.status_code == 200, name
    return {name: response.content.decode() for name, response in pages.items()}


class Fetched(HTMLParser):
    """Every URL a page makes the browser fetch: scripts, styles, fonts, images, frames."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        self.styles: list[str] = []
        self.stylesheets: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" or (tag == "link" and NOT_LOADED & set((values.get("rel") or "").split())):
            return
        for name, value in attrs:
            if name in URL_ATTRIBUTES and value:
                self.urls += [part.split()[0] for part in value.split(",") if part.strip()]
            if name == "style" and value:
                self.styles.append(value)
        if tag == "link" and "stylesheet" in (values.get("rel") or "").split():
            self.stylesheets.append(values["href"] or "")
        self._in_style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.styles.append(data)


def test_production_serves_static_files_under_hashed_names():
    backend = production_storages()["staticfiles"]["BACKEND"]
    assert backend == "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"


def test_every_static_reference_on_a_customer_page_resolves(collected, buyer, plan):
    pages = customer_pages(buyer, plan)
    assert re.search(r"/static/css/app\.[0-9a-f]{12}\.css", pages["home"])
    for name, html in pages.items():
        parser = Fetched()
        parser.feed(html)
        for url in parser.urls:
            path = urlsplit(url).path
            if path.startswith("/static/"):
                assert (collected / path.removeprefix("/static/")).is_file(), (name, url)


def literal_static_references() -> dict[str, list[str]]:
    """Each file name written out in a template's {% static %} or in code's static(), and where.

    A name held in a variable is out of reach of a search, so the page renders above
    stay the check for it.
    """
    found: dict[str, list[str]] = {}
    templates = [*ROOT.glob("templates/**/*"), *ROOT.glob("apps/*/templates/**/*")]
    for template in filter(Path.is_file, templates):
        for match in TEMPLATE_STATIC.finditer(template.read_text(encoding="utf-8")):
            found.setdefault(match["path"], []).append(template.relative_to(ROOT).as_posix())
    sources = [*ROOT.glob("config/**/*.py"), *ROOT.glob("apps/**/*.py")]
    for source in (path for path in sources if "tests" not in path.parts):
        for call in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in {"static", "static_url"}
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                place = f"{source.relative_to(ROOT).as_posix()}:{call.lineno}"
                found.setdefault(call.args[0].value, []).append(place)
    return found


def test_every_literal_static_reference_is_in_the_manifest(collected):
    # The customer pages above are seven of the pages that ask for a file: the order pages,
    # the invoice, the operator panel (Unfold's styles and scripts come from settings) and
    # /favicon.ico would answer 500 in production on a file collectstatic never saw.
    manifest = json.loads((collected / "staticfiles.json").read_text(encoding="utf-8"))["paths"]
    references = literal_static_references()
    places = [place for found in references.values() for place in found]
    assert any(".html" in p for p in places) and any(".py:" in p for p in places), "search broken"
    missing = {n: found for n, found in references.items() if urlsplit(n).path not in manifest}
    assert not missing, f"asked for, but not collected: {missing}"


def test_the_search_reads_both_quotes_and_leaves_a_variable_alone():
    tags = """{% static 'a.css' %}{% static "b.js" %}{% static x|default:'c.png' %}"""
    assert [match["path"] for match in TEMPLATE_STATIC.finditer(tags)] == ["a.css", "b.js"]


def test_no_customer_page_loads_anything_from_another_host(collected, buyer, plan):
    # An asset from another host is a page that breaks when Iran's link to the world is cut.
    for name, html in customer_pages(buyer, plan).items():
        parser = Fetched()
        parser.feed(html)
        css = [
            (collected / urlsplit(href).path.removeprefix("/static/")).read_text(encoding="utf-8")
            for href in parser.stylesheets
        ]
        fetched = parser.urls + [u for text in parser.styles + css for u in CSS_URL.findall(text)]
        assert fetched, f"the {name} page fetched nothing: the parser is broken"
        foreign = [url for url in fetched if urlsplit(url).netloc or url.startswith("//")]
        assert not foreign, (name, foreign)


def test_the_parser_sees_an_asset_from_another_host():
    parser = Fetched()
    parser.feed(
        '<a href="https://t.me/x">t</a><link rel="canonical" href="https://premshop.ir/">'
        '<script src="https://cdn.example/x.js"></script><img srcset="//img.example/a.png 2x">'
        '<style>@font-face{src:url("https://fonts.example/f.woff2")}</style>'
    )
    found = parser.urls + [u for text in parser.styles for u in CSS_URL.findall(text)]
    assert found == [
        "https://cdn.example/x.js",
        "//img.example/a.png",
        "https://fonts.example/f.woff2",
    ]

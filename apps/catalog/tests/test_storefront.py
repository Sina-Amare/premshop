"""The storefront shell: the shelf, the shelves row, the promise, the marks.

These pin the parts of the 2026-10-14 redesign that would fail silently. A
brand mark that quietly reverts to a grey glyph, a promise stated twice with
two different numbers, a Latin product name that loses its bidi isolation —
none of them raise, and all three are the kind of small wrongness this shop
cannot afford on the pages where someone decides whether to pay a stranger.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from apps.catalog.management.commands.seed_catalog import MARKS, PRODUCTS, _normalise_mark
from apps.catalog.models import Category, Plan, Product
from apps.core.context import SUPPORT_HOURS

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category,
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        short_description="اشتراک چت‌جی‌پی‌تی پلاس",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        warranty=Product.Warranty.FULL_PERIOD,
        delivery_hours=12,
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(
        product=product,
        title="۱ ماهه",
        duration_days=30,
        cost_price=Decimal(850000),
        sale_price=Decimal(1390000),
    )
    return product


# --- The shelf ---------------------------------------------------------------


def test_the_hero_shows_every_service_and_each_mark_is_a_link(client, shop):
    """The constellation in the hero is the catalogue, not decoration: it
    answers «do you have it» in a glance, so every active product is in it and
    every tile goes to that product."""
    body = client.get(reverse("home")).content.decode()

    assert body.count('class="nebula__item"') == Product.objects.public().count()
    assert f'class="nebula__item" href="{reverse("product", args=[shop.slug])}"' in body


def test_every_mark_in_the_hero_has_an_accessible_name(client, shop):
    """A link whose only content is an image has no name without one. These
    tiles are the first ten links on the page; empty alt text would make the
    whole hero unreadable to a screen reader and invisible to search."""
    body = client.get(reverse("home")).content.decode()

    hero = body[body.index("nebula") : body.index("promises")]

    assert f'title="{shop.name}"' in hero, "no hover name on the tile"
    # With a mark it is the image's alt; without one it is visually hidden text.
    assert shop.name in hero, "the service is unnamed to a screen reader"


def test_a_draft_product_is_not_in_the_hero(client, shop):
    Product.objects.create(
        category=shop.category,
        name="Secret",
        slug="secret",
        delivery_type=Product.DeliveryType.CODE_LICENSE,
        delivery_hours=24,
        status=Product.Status.DRAFT,
    )

    body = client.get(reverse("home")).content.decode()

    assert "Secret" not in body
    assert body.count('class="nebula__item"') == 1


def test_the_shelves_row_lists_only_departments_that_have_something_in_them(client, shop):
    """An empty department in the header is a dead end dressed as a section."""
    Category.objects.create(name="خالی", slug="empty")

    body = client.get(reverse("home")).content.decode()

    assert "هوش مصنوعی" in body
    assert "خالی" not in body


# --- One promise, one number -------------------------------------------------


def test_the_answering_hours_are_stated_once_and_agree_everywhere(client, shop):
    """The header strip, the footer and the product FAQ all claim the shop's
    answering hours. Three literals would eventually be three different
    numbers, and on a shop with no نماد اعتماد that is a trust defect, not a
    typo — so all three read the same context value."""
    for url in [reverse("home"), reverse("product", args=[shop.slug]), reverse("faq")]:
        body = client.get(url).content.decode()
        assert SUPPORT_HOURS in body, url
        # Any other opening-hours range means a second literal crept back.
        # A range followed by «ساعت» is a delivery window («۱ تا ۴۸ ساعت»),
        # which is a different fact and is allowed to differ.
        ranges = set(re.findall(r"[۰-۹]+ تا [۰-۹]+(?![۰-۹])(?! ساعت)", body))
        assert ranges <= {SUPPORT_HOURS}, (url, ranges)


def test_the_footer_links_somewhere_that_exists(client, shop):
    """Every footer link used to point at `/`. A buyer who clicks «شرایط
    بازگشت وجه» and lands back on the home page has been told something."""
    body = client.get(reverse("home")).content.decode()
    footer = body[body.index('class="ftr__links"') :]
    hrefs = re.findall(r'href="([^"]+)"', footer[: footer.index("</nav>")])

    assert hrefs
    for href in hrefs:
        assert client.get(href).status_code == 200, href


def test_the_faq_answers_the_questions_a_buyer_asks_before_paying(client, shop):
    body = client.get(reverse("faq")).content.decode()

    assert "<details" in body, "it must open without JavaScript"
    # The questions buyers in this market actually ask, from the FAQ pages of
    # shops already selling these products. «باید حساب بسازم؟» and «پرداخت
    # چطور انجام می‌شود؟» were here and are not questions anyone has.
    for topic in ["رمز اکانت", "اشتراکیه", "تحویل", "گارانتی", "برمی‌گرده", "درگاه بانکی"]:
        assert topic in body, topic
    for obvious in ["باید حساب بسازم", "بله، چون"]:
        assert obvious not in body, obvious


# --- Bidi --------------------------------------------------------------------

BIDI_PAGES = ["home", "search", "faq"]


# Each assertion names the element, not just the page. An earlier version of
# this test looked for the isolated name anywhere in the body, and passed with
# the card's isolation deleted — the shelf's copy of the same name satisfied
# it. A guard that another element can satisfy is not guarding anything.
ISOLATED = [
    ("home", 'class="pcard__name"><bdi>{name}</bdi>'),
    ("product", 'class="pdp__title"><bdi>{name}</bdi>'),
]


@pytest.mark.parametrize(("page", "shape"), ISOLATED)
def test_every_latin_product_name_is_isolated(client, shop, page, shape):
    """Unisolated, «اشتراک Netflix.» puts the full stop on the wrong side and
    «(Spotify)» renders «)Spotify(». Every product name in this shop is a
    Latin run inside a Persian sentence, so the isolation is not optional."""
    url = reverse(page, args=[shop.slug] if page == "product" else [])

    body = client.get(url).content.decode()

    assert shape.format(name=shop.name) in body


def test_a_mixed_name_is_isolated_too(client, shop):
    """bdi infers direction per run, so wrapping is unconditional — a template
    that only isolates names it thinks are Latin is a template that will meet
    a mixed name and get it wrong."""
    shop.name = "اشتراک Netflix"
    shop.save()

    body = client.get(reverse("home")).content.decode()

    assert 'class="pcard__name"><bdi>اشتراک Netflix</bdi>' in body


# --- The brand marks ---------------------------------------------------------


def test_every_seeded_product_has_a_mark_source():
    """The seeder used to build one CDN URL from a slug, and three brands were
    404 there — which is why ChatGPT, Adobe and Midjourney rendered as a
    letter. One source per brand means this mapping must stay complete."""
    assert {p["slug"] for p in PRODUCTS} == set(MARKS)


def test_the_marks_are_normalised_so_a_shelf_of_them_looks_even():
    """Padding is applied by widening the viewBox around its own centre: the
    artwork shrinks inside an unchanged box and no path is touched."""
    svg = b'<?xml version="1.0"?><svg viewBox="0 0 256 200" fill="#FA0F00"><path d="M0 0"/></svg>'

    padded = _normalise_mark(svg, 0.25)

    assert b'viewBox="-64 -50 384 300"' in padded
    assert b'fill="#FA0F00"' in padded, "colour must survive — it is the point"
    assert b'<path d="M0 0"/>' in padded
    # A mark that already carries its own coloured container is left alone.
    assert _normalise_mark(svg, 0) == svg


def test_a_mark_without_a_viewbox_is_passed_through_rather_than_mangled():
    svg = b'<svg><path d="M0 0"/></svg>'

    assert _normalise_mark(svg, 0.2) == svg


@pytest.mark.skipif(
    not (Path(settings.MEDIA_ROOT) / "products").exists(), reason="catalog not seeded"
)
def test_the_downloaded_marks_are_svgs_in_their_own_colours():
    """A positive control over the files the seeder actually wrote. The old
    seeder cached a failed fetch forever, so an HTML error page could sit in
    MEDIA_ROOT named `.svg` and nothing would ever notice."""
    files = list((Path(settings.MEDIA_ROOT) / "products").glob("*.svg"))
    assert files, "seed the catalog first"

    coloured = 0
    for path in files:
        body = path.read_bytes()
        assert b"<svg" in body[:400], path.name
        assert b"viewBox" in body, path.name
        assert b"<html" not in body[:400].lower(), f"{path.name} is an error page"
        if re.search(rb"#(?!000000\b|000\b)[0-9A-Fa-f]{3,6}", body):
            coloured += 1
    # Apple, OpenAI and GitHub Copilot really are black marks; most are not.
    assert coloured >= len(files) - 3, f"only {coloured}/{len(files)} marks carry a brand colour"


# --- Performance budget ------------------------------------------------------


def test_the_web_fonts_stay_inside_their_budget():
    """Fonts were 81% of the home page's bytes before they were subset. This
    is a budget, not a measurement: a full unsubset face dropped back into
    static/fonts/ would otherwise cost 180 KB and nobody would see it until a
    customer on a slow link did."""
    total = sum(f.stat().st_size for f in (settings.BASE_DIR / "static" / "fonts").glob("*.woff2"))

    assert total < 160_000, f"{total} bytes of web fonts; subset them (scripts/subset_fonts.py)"


def test_every_preloaded_face_is_one_the_shell_actually_paints(client, shop):
    """The page preloaded Estedad-ExtraBold — which only the price numeral
    uses — while every heading waited on Estedad-SemiBold, discovered a round
    trip later. Preloading a face nobody needs is worse than not preloading."""
    body = client.get(reverse("home")).content.decode()
    preloaded = set(re.findall(r'rel="preload" href="[^"]*fonts/([^"?]+?)\.woff2', body))
    css = (settings.BASE_DIR / "static" / "css" / "input.css").read_text(encoding="utf-8")

    assert preloaded, "the shell preloads no fonts at all"
    for face in preloaded:
        assert f"{face}.woff2" in css, f"{face} is preloaded but no @font-face uses it"

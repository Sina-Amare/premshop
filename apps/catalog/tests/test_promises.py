"""What the shop says on every page is true of every product it sells.

The header strip promised «گارانتی تا پایان دوره» above a gift card with no
warranty, and the footer promised a bank gateway S5 has not built and a
money-back guarantee nobody had written. A line printed on every page is read
as true of whatever the visitor is looking at, so these are checked against
the catalogue rather than read for tone.
"""

from __future__ import annotations

from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from apps.catalog.models import Category, Plan, Product
from apps.catalog.promises import DELIVERY_CAP_HOURS, PROMISES
from apps.core import support

pytestmark = pytest.mark.django_db

# Where the shell prints promises: the strip over every page, the footer row
# and the home page's row.
SITE_WIDE = ("v-support", "v-footer__contact", "v-hero__proof")

# Claims nothing backs yet. Payment waits on the gateway (S5) and on the
# owner's ruling about what to say before it; refunds and warranty terms on
# the owner's rulings too.
UNBACKED = ("درگاه", "بازگشت وجه", "ضمانت", "برمی‌گرده", "سریع‌ترین", "گارانتی")


@pytest.fixture
def catalogue():
    """One product of every shape a promise has to survive."""
    subs = Category.objects.create(name="هوش مصنوعی", slug="ai")
    gifts = Category.objects.create(name="گیفت کارت", slug="gift")
    shapes = [
        ("full", subs, Product.DeliveryType.ON_CUSTOMER_ACCOUNT, Product.Warranty.FULL_PERIOD, 1),
        ("week", subs, Product.DeliveryType.READY_ACCOUNT, Product.Warranty.DAYS_7, 24),
        ("card", gifts, Product.DeliveryType.GIFT_CARD, Product.Warranty.NONE, DELIVERY_CAP_HOURS),
        ("code", gifts, Product.DeliveryType.CODE_LICENSE, Product.Warranty.NONE, 48),
    ]
    for slug, category, delivery, warranty, hours in shapes:
        product = Product.objects.create(
            category=category,
            name=slug,
            slug=slug,
            delivery_type=delivery,
            warranty=warranty,
            delivery_hours=hours,
            status=Product.Status.ACTIVE,
        )
        Plan.objects.create(
            product=product, title="۱ ماهه", cost_price=Decimal(1), sale_price=Decimal(100000)
        )
    return Product.objects.public()


class _Regions(HTMLParser):
    """The text inside every element carrying one of the given classes."""

    VOID = {"area", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}

    def __init__(self, classes: tuple[str, ...]) -> None:
        super().__init__()
        self.classes, self.depth, self.found = set(classes), 0, {c: "" for c in classes}
        self.current = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID:
            return
        if self.depth:
            self.depth += 1
            return
        hit = self.classes & set((dict(attrs).get("class") or "").split())
        if hit:
            self.current, self.depth = hit.pop(), 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth and tag not in self.VOID:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.found[self.current] += data


def regions(body: str) -> dict[str, str]:
    parser = _Regions(SITE_WIDE)
    parser.feed(body)
    return {name: " ".join(text.split()) for name, text in parser.found.items()}


def pages(catalogue) -> list[str]:
    return [
        reverse("home"),
        reverse("faq"),
        reverse("search") + "?q=zzz",
        *(reverse("category", args=[c]) for c in ("ai", "gift")),
        *(reverse("product", args=[p.slug]) for p in catalogue),
    ]


def test_every_site_wide_promise_holds_for_every_public_product(catalogue):
    assert PROMISES, "the shop promises nothing at all; the controls below would pass on air"
    for promise in PROMISES:
        for product in catalogue:
            assert promise.holds_for(product), (promise.text, product.slug)


def test_the_delivery_cap_is_the_one_the_database_enforces(catalogue):
    """The promise quotes a number; the CHECK is what keeps it true."""
    from django.db import IntegrityError, transaction

    product = catalogue.get(slug="card")
    product.delivery_hours = DELIVERY_CAP_HOURS + 1
    with pytest.raises(IntegrityError), transaction.atomic():
        product.save()


def test_the_shell_prints_only_what_the_source_holds(client, catalogue):
    """Every word in the site-wide regions comes from the promise source or the
    support details. A literal typed into a template, the way «گارانتی تا
    پایان دوره» was, leaves words over and fails here."""
    allowed = [
        support.details()["hours"],
        support.details()["telegram"],
        "پشتیبانی",
        "·",
        *(p.text for p in PROMISES),
    ]
    seen = set()
    for url in pages(catalogue):
        for name, text in regions(client.get(url).content.decode()).items():
            if not text:
                continue
            seen.add(name)
            rest = text
            for phrase in sorted(allowed, key=len, reverse=True):
                rest = rest.replace(phrase, "")
            assert not rest.strip(), f"{url} {name}: {rest.strip()!r}"
    assert seen == set(SITE_WIDE), f"a region went missing, so it was never checked: {seen}"


@pytest.mark.parametrize("claim", UNBACKED)
def test_no_page_makes_a_claim_nothing_backs(client, catalogue, claim):
    for url in pages(catalogue):
        body = client.get(url).content.decode()
        for name, text in regions(body).items():
            assert claim not in text, f"{claim} in {name} on {url}"
        if claim != "گارانتی":  # a product's own warranty is its operator's fact
            assert claim not in body, f"{claim} on {url}"


def test_the_region_reader_sees_a_planted_promise():
    """The control for the reader: a promise typed straight into the strip."""
    planted = (
        '<div class="v-support"><div class="wrap">پشتیبانی · گارانتی تا پایان دوره</div></div>'
    )

    assert "گارانتی تا پایان دوره" in regions(planted)["v-support"]

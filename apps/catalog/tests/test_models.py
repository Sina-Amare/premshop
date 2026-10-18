"""The catalog's rules: one price function, the database CHECKs, and what a visitor may see."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import FUZZY_THRESHOLD, Category, Plan, Product
from apps.catalog.pricing import effective_price

pytestmark = pytest.mark.django_db

NOW = timezone.now()


@pytest.fixture
def product():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    return Product.objects.create(
        category=category,
        name="Claude Pro",
        slug="claude-pro",
        short_description="اشتراک کلاود پرو",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        status=Product.Status.ACTIVE,
    )


def plan(product, **kwargs) -> Plan:
    kwargs.setdefault("title", "۱ ماهه")
    kwargs.setdefault("cost_price", Decimal("900000"))
    kwargs.setdefault("sale_price", Decimal("1450000"))
    return Plan.objects.create(product=product, **kwargs)


# --- The one price rule -----------------------------------------------------


def test_effective_price_inside_window_returns_promo(product):
    p = plan(
        product,
        promo_price=Decimal("1200000"),
        promo_starts_at=NOW - timedelta(days=1),
        promo_ends_at=NOW + timedelta(days=1),
    )
    assert effective_price(p, at=NOW) == Decimal("1200000")
    assert p.promotion_is_active


def test_effective_price_outside_window_and_unset_returns_sale_price(product):
    ended = plan(
        product,
        promo_price=Decimal("1200000"),
        promo_starts_at=NOW - timedelta(days=2),
        promo_ends_at=NOW - timedelta(days=1),
    )
    not_yet = plan(
        product,
        title="۳ ماهه",
        promo_price=Decimal("1200000"),
        promo_starts_at=NOW + timedelta(days=1),
        promo_ends_at=NOW + timedelta(days=2),
    )
    unset = plan(product, title="۱۲ ماهه")

    assert effective_price(ended, at=NOW) == Decimal("1450000")
    assert effective_price(not_yet, at=NOW) == Decimal("1450000")
    assert effective_price(unset, at=NOW) == Decimal("1450000")
    assert not unset.promotion_is_active


@pytest.mark.parametrize(
    "starts, ends",
    [
        (None, None),  # both open: a promotion until further notice
        (None, NOW + timedelta(days=1)),  # no start: already started
        (NOW - timedelta(days=1), None),  # no end: never ends
    ],
)
def test_effective_price_open_ended_bounds(product, starts, ends):
    p = plan(product, promo_price=Decimal("1000000"), promo_starts_at=starts, promo_ends_at=ends)
    assert effective_price(p, at=NOW) == Decimal("1000000")


def test_the_window_is_half_open(product):
    """A promotion ending at 10:00 is over at 10:00, not at 10:00:59."""
    end = NOW
    p = plan(product, promo_price=Decimal("1000000"), promo_ends_at=end)
    assert effective_price(p, at=end - timedelta(seconds=1)) == Decimal("1000000")
    assert effective_price(p, at=end) == Decimal("1450000")


# --- The two promotion CHECKs, proven at the database, not the form -----------


@pytest.mark.parametrize(
    "bad", [Decimal("0"), Decimal("-5"), Decimal("1450000"), Decimal("2000000")]
)
def test_promo_price_check_rejects_zero_negative_and_above_sale_price(product, bad):
    with pytest.raises(IntegrityError), transaction.atomic():
        plan(product, promo_price=bad)


def test_promo_window_check_rejects_start_after_end(product):
    with pytest.raises(IntegrityError), transaction.atomic():
        plan(
            product,
            promo_price=Decimal("1000000"),
            promo_starts_at=NOW,
            promo_ends_at=NOW - timedelta(hours=1),
        )


def test_the_checks_reach_the_admin_form_in_persian(product):
    """Django validates CheckConstraints in full_clean, so the admin shows the
    database's rule as a Persian sentence — no custom clean() to drift from it."""
    from django.core.exceptions import ValidationError

    p = Plan(product=product, title="x", cost_price=1, sale_price=1000, promo_price=1000)
    with pytest.raises(ValidationError) as excinfo:
        p.full_clean()
    assert "قیمت تخفیفی" in str(excinfo.value)


# --- The rest of data-model §3's CHECKs --------------------------------------
# The contract specified these for the catalog at S3; the S3 migration shipped
# with only the two promotion checks. Added 2026-09-21 (catalog 0002).


@pytest.mark.parametrize("hours", [0, -1, 73])
def test_delivery_hours_check_keeps_the_promise_inside_the_cap(product, hours):
    """Every product page promises delivery within its hours, and the FAQ
    quotes the cap to customers. Zero would read as instant; anything past
    the cap breaks a printed promise. Raised 48 -> 72 on 2026-10-18
    (ADR-0009, amended)."""
    product.delivery_hours = hours
    with pytest.raises(IntegrityError), transaction.atomic():
        product.save()


@pytest.mark.parametrize("hours", [1, 48, 72])
def test_the_cap_itself_is_allowed(product, hours):
    """The boundary, which the refusals above cannot show: a test that only
    ever checks what is rejected passes just as well with the whole field
    rejected."""
    product.delivery_hours = hours
    product.save()
    product.refresh_from_db()
    assert product.delivery_hours == hours


def test_status_check_refuses_a_value_outside_the_three(product):
    """`choices=` binds the admin form only; a shell or an import can write anything,
    and a product in an unknown status is neither public nor visibly a draft."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.filter(pk=product.pk).update(status="published")


@pytest.mark.parametrize("field", ["cost_price", "sale_price"])
def test_prices_cannot_be_negative(product, field):
    with pytest.raises(IntegrityError), transaction.atomic():
        plan(product, **{field: Decimal("-1")})


@pytest.mark.parametrize("days", [0, -30])
def test_a_plan_lasts_at_least_a_day_or_never_expires(product, days):
    """NULL means never expires. Zero or negative would put an expiry date on or
    before the delivery date, and the renewal reminder would fire at once."""
    with pytest.raises(IntegrityError), transaction.atomic():
        plan(product, duration_days=days)


def test_the_new_checks_reach_the_admin_form_in_persian(product):
    from django.core.exceptions import ValidationError

    p = Plan(product=product, title="x", cost_price=-1, sale_price=1000)
    with pytest.raises(ValidationError) as excinfo:
        p.full_clean()
    assert "منفی" in str(excinfo.value)


# --- What a visitor may see ---------------------------------------------------


def test_draft_product_hidden(product):
    Product.objects.create(
        category=product.category,
        name="پیش‌نویس",
        slug="draft",
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=Product.Status.DRAFT,
    )
    Product.objects.create(
        category=product.category,
        name="ناموجود",
        slug="gone",
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        status=Product.Status.UNAVAILABLE,
    )

    assert list(Product.objects.public()) == [product]


def test_search_text_is_maintained_on_save(product):
    assert product.search_text == "claude pro اشتراک کلاود پرو"
    product.name = "Claude Max"
    product.save()
    assert product.search_text.startswith("claude max")


def test_search_finds_arabic_keyboard_input(product):
    assert list(Product.objects.public().search("كلاود")) == [product]  # arabic kaf
    assert list(Product.objects.public().search("CLAUDE")) == [product]
    assert list(Product.objects.public().search("")) == []


@pytest.fixture
def chatgpt(product):
    """The case the owner found: a Persian shopper types a transliteration that
    shares no substring with the product's own name."""
    return Product.objects.create(
        category=product.category,
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        short_description="اشتراک چت‌جی‌پی‌تی پلاس روی اکانت خودتان.",
        aliases="چت جی پی تی، چت جیپیتی، چت جیپت، چت گپت، جیپیتی، gpt، openai",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        status=Product.Status.ACTIVE,
    )


def test_aliases_carry_the_spellings_a_customer_actually_types(chatgpt):
    assert "چت جیپت" in chatgpt.search_text
    assert list(Product.objects.public().search("چت جیپت")) == [chatgpt]


def test_every_word_is_matched_separately(chatgpt):
    """Word order and extra words must not decide whether a product is found:
    before 2026-10-13 the whole query had to appear as one contiguous substring."""
    assert list(Product.objects.public().search("جیپت چت")) == [chatgpt]
    assert list(Product.objects.public().search("چت جیپت پلاس")) == [chatgpt]


def test_a_word_matching_nothing_now_falls_through_to_the_closest_product(chatgpt):
    """This returned nothing until the trigram pass existed, and the change is
    deliberate. The AND is still strict — no product contains both names, so
    the exact pass finds nothing — but a miss now offers the nearest product
    instead of an empty page. Someone who typed two product names at once is
    better served by one of them than by neither."""
    assert list(Product.objects.public().search("چت جیپت نتفلیکس")) == [chatgpt]


def test_aliases_never_reach_the_visitor(client, chatgpt):
    """They are search fuel, not copy: «چت گپت» on the page would read as a typo."""
    body = client.get(f"/p/{chatgpt.slug}/").content.decode()

    assert "چت گپت" not in body
    assert "openai" not in body.lower()


def test_slug_is_filled_from_the_name_when_blank():
    category = Category.objects.create(name="ابزار توسعه")
    assert category.slug == "ابزار-توسعه"
    product = Product.objects.create(
        category=category, name="GitHub Copilot", delivery_type="on_customer_account"
    )
    assert product.slug == "github-copilot"


def test_str(product):
    p = plan(product)
    assert str(product) == "Claude Pro"
    assert str(p) == "Claude Pro — ۱ ماهه"
    assert str(product.category) == "هوش مصنوعی"


def test_a_category_with_products_cannot_be_deleted(product):
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        product.category.delete()


# --- Spellings nobody listed -------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "چت جپت",  # the ی dropped — the spelling that started this
        "چتجیپیتی",  # run together, no spaces
        "چت جی پی تی",  # on the alias list, so the exact pass answers
        "chatgtp",  # Latin, two letters transposed
    ],
)
def test_a_spelling_that_is_not_on_the_alias_list_still_finds_it(chatgpt, typed):
    """`aliases` is a hand-written list and can never be finished. «چت جیپت»
    was on it and «چت جپت» was not, so one real spelling of ChatGPT found the
    product and the other found nothing. A trigram compares the shapes of the
    letters, which is the only thing that catches a transliteration the
    operator never thought of."""
    assert list(Product.objects.public().search(typed)) == [chatgpt]


def test_an_exact_match_is_never_diluted_by_near_ones(chatgpt):
    """The fuzzy pass runs only when the exact one finds nothing. A query
    that had a real answer must not pick up neighbours as well."""
    Product.objects.create(
        category=chatgpt.category,
        name="ChatGPT Team",
        slug="chatgpt-team",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        delivery_hours=12,
        status=Product.Status.ACTIVE,
    )

    found = list(Product.objects.public().search("chatgpt plus"))

    assert found == [chatgpt]


@pytest.mark.parametrize("nonsense", ["zzzqqq", "کفش", "سلام", "qwertyuiop"])
def test_nonsense_still_finds_nothing(chatgpt, nonsense):
    """A fuzzy search that answers everything is worse than one that answers
    nothing: it would put ChatGPT in front of someone looking for shoes."""
    assert list(Product.objects.public().search(nonsense)) == []


def test_the_fuzzy_threshold_separates_real_spellings_from_noise(chatgpt):
    """The number in FUZZY_THRESHOLD is measured, and this is the measurement.
    If a future catalogue narrows the gap, this fails before a customer finds
    out by being shown the wrong product."""
    from django.contrib.postgres.search import TrigramWordSimilarity

    def closeness(text: str) -> float:
        return (
            Product.objects.filter(pk=chatgpt.pk)
            .annotate(score=TrigramWordSimilarity(text, "search_text"))
            .values_list("score", flat=True)[0]
        )

    real = [closeness(q) for q in ["چت جپت", "چتجیپیتی", "chatgtp", "چت گپت"]]
    noise = [closeness(q) for q in ["zzzqqq", "کفش", "سلام", "نتفلیکس"]]

    assert min(real) >= FUZZY_THRESHOLD, f"a real spelling scores {min(real):.2f}"
    assert max(noise) < FUZZY_THRESHOLD, f"noise scores {max(noise):.2f}"
    # And the gap is wide enough that the threshold is not balanced on a knife
    # edge — if this shrinks, the two populations have started to overlap.
    assert min(real) - max(noise) > 0.1

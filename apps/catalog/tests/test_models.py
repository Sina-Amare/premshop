"""The catalog's rules: one price function, the database CHECKs, and what a visitor may see."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from PIL import ExifTags, Image, PngImagePlugin

from apps.catalog.models import (
    FUZZY_THRESHOLD,
    Category,
    Plan,
    Product,
    ProductQuestion,
    ProductSection,
    ProductSpec,
)
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
    assert "قیمت با تخفیف" in str(excinfo.value)


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


def test_a_latin_name_fills_an_empty_slug():
    category = Category.objects.create(name="ابزار توسعه", slug="dev")
    product = Product.objects.create(
        category=category, name="GitHub Copilot", delivery_type="on_customer_account"
    )
    assert product.slug == "github-copilot"


def test_a_persian_name_never_becomes_a_slug():
    """Slugs are Latin. A Persian one is percent-encoded into a line of %D8
    wherever the address is pasted, so a Persian name asks for its slug."""
    from django.core.exceptions import ValidationError

    category = Category.objects.create(name="ابزار توسعه", slug="dev")
    product = Product(category=category, name="گیت‌هاب کوپایلت", delivery_type="code_license")

    with pytest.raises(ValidationError) as excinfo:
        product.full_clean()

    assert "slug" in excinfo.value.message_dict


@pytest.mark.parametrize("name", ["ابزار توسعه", "!!!"])
def test_a_save_with_no_slug_to_give_is_refused(name):
    """Outside a form, from the shell or a script, save() made «ابزار-توسعه»
    out of a Persian name, and later an empty slug. An empty slug reverses to no
    page: one category with it broke the header on every page."""
    with pytest.raises(ValueError, match="slug"):
        Category.objects.create(name=name)
    category = Category.objects.create(name="ابزار توسعه", slug="dev")
    with pytest.raises(ValueError, match="slug"):
        Product.objects.create(category=category, name=name, delivery_type="code_license")


def test_a_persian_slug_is_refused(product):
    from django.core.exceptions import ValidationError

    product.slug = "کلاود-پرو"
    with pytest.raises(ValidationError) as excinfo:
        product.full_clean()

    assert "slug" in excinfo.value.message_dict


@pytest.mark.parametrize(
    "title", ["خرید Claude Pro | PremShop", "خرید Claude Pro — premshop", "PremShop: Claude Pro"]
)
def test_an_seo_title_leaves_the_shops_name_to_the_page(product, title):
    """base.html adds « | PremShop» to every title, so a field that already
    holds it puts the shop's name in the tab twice."""
    for row in (product, product.category):
        row.seo_title = title
        with pytest.raises(ValidationError) as excinfo:
            row.full_clean()
        assert "seo_title" in excinfo.value.message_dict, type(row).__name__


def test_an_seo_title_without_the_shops_name_is_kept(product):
    product.seo_title = product.category.seo_title = "خرید اشتراک Claude Pro"

    product.full_clean()
    product.category.full_clean()


@pytest.mark.parametrize(
    "fields, under",
    [
        ({"promo_price": Decimal("2000000")}, "promo_price"),
        ({"sale_price": Decimal("-1")}, "sale_price"),
        ({"duration_days": 0}, "duration_days"),
        ({"max_quantity": 11}, "max_quantity"),
        ({"requires_customer_input": True, "max_quantity": 2}, "max_quantity"),
        (
            {"promo_price": Decimal("1000"), "promo_starts_at": NOW, "promo_ends_at": NOW},
            "promo_starts_at",
        ),
    ],
)
def test_a_check_is_reported_under_the_field_it_is_about(product, fields, under):
    """A CheckConstraint names no field, so Django filed every one of these
    above the form, where Unfold's plan inline prints nothing."""
    from django.core.exceptions import ValidationError

    p = Plan(product=product, title="x", **{"cost_price": 1, "sale_price": 1450000, **fields})
    with pytest.raises(ValidationError) as excinfo:
        p.full_clean()

    assert list(excinfo.value.message_dict) == [under]


def test_the_delivery_cap_is_reported_under_delivery_hours(product):
    from django.core.exceptions import ValidationError

    product.delivery_hours = 73
    with pytest.raises(ValidationError) as excinfo:
        product.full_clean()

    assert list(excinfo.value.message_dict) == ["delivery_hours"]


def test_str(product):
    """«۱ ماهه، Claude Pro»: the Latin name isolated, so the bidi algorithm
    cannot carry the plan's words to the far side of it."""
    p = plan(product)
    assert str(product) == "Claude Pro"
    assert str(p) == "۱ ماهه، \u2068Claude Pro\u2069"
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


# --- Publishing ----------------------------------------------------------------


@pytest.fixture
def ready(product):
    """A draft with every fact publishing asks for, and nothing left to decide."""
    product.status = Product.Status.DRAFT
    product.usage_notes = "- روی ایمیل خودتان فعال می‌شود."
    product.save()
    plan(product)
    return product


def test_a_product_with_every_fact_may_publish(ready):
    """The control: a rule that refuses everything passes every refusal below."""
    assert ready.publish_problems() == []


@pytest.mark.parametrize("notes", ["", "   "])
def test_publishing_needs_usage_notes(ready, notes):
    ready.usage_notes = notes

    assert ready.publish_problems() == [
        "نکات استفاده رو بنویسید. اگه نکته‌ای نداره، عبارت «موضوعیت ندارد» رو وارد کنید."
    ]


@pytest.mark.parametrize("notes", ["موضوعیت ندارد", "موضوعيت ندارد."])
def test_not_applicable_is_a_note(ready, notes):
    """A product with nothing to add says so, in either spelling of ی."""
    ready.usage_notes = notes

    assert ready.publish_problems() == []
    assert ready.usage_notes_shown == ""


def test_usage_notes_ask_for_no_source(ready):
    """The notes are the shop's own words, not a claim about a provider's terms."""
    assert ready.sources == ""
    assert ready.publish_problems() == []


@pytest.mark.parametrize(
    "field, problem",
    [
        ("delivery_type", "نوع تحویل رو انتخاب کنید."),
        ("short_description", "معرفی کوتاه محصول رو بنویسید."),
    ],
)
def test_each_missing_fact_is_named(ready, field, problem):
    setattr(ready, field, "")

    assert ready.publish_problems() == [problem]


def test_publishing_needs_a_plan_on_sale(ready):
    ready.plans.update(is_available=False)

    assert ready.publish_problems() == ["برای انتشار، حداقل یک پلن با وضعیت «موجود» لازمه."]


MARKERS = ["[تصمیم مالک، تصمیم ۴: چند روز]", "[وابسته به تصمیم ۸: …]", "[از تحویل آزمایشی: …]"]


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("where", ["product", "section", "question", "spec", "plan"])
def test_a_placeholder_anywhere_on_the_page_holds_publishing(ready, marker, where):
    text = f"مبلغ را ظرف {marker} برمی‌گردانیم."
    if where == "product":
        ready.refund_note = text
    elif where == "section":
        ProductSection.objects.create(product=ready, kind="before_buying", body=text)
    elif where == "question":
        ProductQuestion.objects.create(product=ready, question="پول برمی‌گرده؟", answer=text)
    elif where == "spec":
        ProductSpec.objects.create(product=ready, title="بازگشت وجه", value=text)
    else:
        ready.plans.update(customer_input_label=text)

    [problem] = ready.publish_problems()

    assert marker in problem, problem


def test_a_placeholder_is_named_with_its_section_and_field(ready):
    ProductSection.objects.create(product=ready, kind="before_buying", body="[تصمیم مالک، تصمیم ۴]")

    assert ready.publish_problems() == [
        "بخش محتوا «\u2068قبل از خرید\u2069»، متن: جای خالی «[تصمیم مالک، تصمیم ۴]» رو کامل کنید."
    ]


def test_a_bracket_that_is_not_a_placeholder_publishes(ready):
    ProductSection.objects.create(product=ready, kind="free", body="جدول [پایین] را ببینید.")

    assert ready.publish_problems() == []


@pytest.mark.parametrize("days_ago, fresh", [(0, True), (7, True), (8, False), (None, False)])
def test_a_time_sensitive_product_needs_a_check_this_week(ready, days_ago, fresh):
    ready.time_sensitive = True
    if days_ago is not None:
        ready.facts_checked_on = timezone.localdate() - timedelta(days=days_ago)

    problems = ready.publish_problems()

    if fresh:
        assert problems == []
    else:
        assert len(problems) == 1 and "۷ روز" in problems[0], problems


def test_a_product_that_is_not_time_sensitive_needs_no_check(ready):
    assert ready.facts_checked_on is None
    assert ready.publish_problems() == []


def test_publishing_through_full_clean_names_the_field_to_fix(ready):
    """The product form's path: a draft set to active is refused field by field."""
    ready.usage_notes = ""
    ready.status = Product.Status.ACTIVE

    with pytest.raises(ValidationError) as excinfo:
        ready.full_clean()

    assert set(excinfo.value.message_dict) == {"usage_notes", "status"}


def test_a_live_product_is_held_to_its_placeholders_only(product):
    """A fact that went stale on a live product must not lock every other
    edit; an unresolved placeholder must never reach the page."""
    assert product.usage_notes == "", "the fixture is live and has no notes"
    product.full_clean()

    product.short_description = "[از تحویل آزمایشی: …]"
    with pytest.raises(ValidationError) as excinfo:
        product.full_clean()

    assert set(excinfo.value.message_dict) == {"short_description", "status"}


def test_a_check_date_cannot_be_in_the_future(product):
    product.facts_checked_on = timezone.localdate() + timedelta(days=1)

    with pytest.raises(ValidationError) as excinfo:
        product.full_clean()

    assert list(excinfo.value.message_dict) == ["facts_checked_on"]


def test_sources_are_one_address_a_line(product):
    product.sources = "https://openai.com/chatgpt/pricing/\n\nhttps://help.openai.com/x"
    product.full_clean()

    product.sources = "https://openai.com/chatgpt/pricing/\nصفحه‌ی قیمت‌ها"
    with pytest.raises(ValidationError) as excinfo:
        product.full_clean()

    assert "خط ۲" in excinfo.value.message_dict["sources"][0]


# --- When a price was last confirmed ---------------------------------------------


def test_a_new_plan_is_confirmed_as_it_is_created(product):
    assert plan(product).price_confirmed_at is not None


@pytest.mark.parametrize("field", ["cost_price", "sale_price", "promo_price"])
def test_changing_a_price_confirms_it(product, field):
    p = plan(product)
    Plan.objects.filter(pk=p.pk).update(price_confirmed_at=NOW - timedelta(days=30))
    p.refresh_from_db()

    setattr(p, field, Decimal("1000000"))
    p.save()
    p.refresh_from_db()

    assert p.price_confirmed_at > NOW


def test_an_edit_that_leaves_the_prices_leaves_the_confirmation(product):
    """Renaming a plan says nothing about whether its price is still right."""
    p = plan(product)
    old = NOW - timedelta(days=30)
    Plan.objects.filter(pk=p.pk).update(price_confirmed_at=old)
    p.refresh_from_db()

    p.title = "یک ماهه"
    p.save()
    p.is_available = False
    p.save(update_fields=["is_available"])
    p.refresh_from_db()

    assert p.price_confirmed_at == old


def test_a_price_saved_with_update_fields_is_confirmed_too(product):
    p = plan(product)
    Plan.objects.filter(pk=p.pk).update(price_confirmed_at=None)
    p.refresh_from_db()

    p.sale_price = Decimal("1500000")
    p.save(update_fields=["sale_price"])
    p.refresh_from_db()

    assert p.price_confirmed_at is not None


# --- Sections ------------------------------------------------------------------


def test_a_product_has_one_before_buying_section_at_most(product):
    """The conditions of a sale are said once. Two would say them twice, or
    disagree."""
    ProductSection.objects.create(product=product, kind="before_buying")
    second = ProductSection(product=product, kind="before_buying")

    with pytest.raises(ValidationError) as excinfo:
        second.full_clean()
    assert list(excinfo.value.message_dict) == ["kind"]
    with pytest.raises(IntegrityError), transaction.atomic():
        second.save()
    # Every other kind repeats freely.
    ProductSection.objects.create(product=product, kind="free")
    ProductSection.objects.create(product=product, kind="free")


def test_a_section_kind_outside_the_six_is_refused_by_the_database(product):
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductSection.objects.create(product=product, kind="hero")


def test_an_image_needs_its_alternative_text(product):
    section = ProductSection(product=product, kind="after_payment", image="sections/x.png")

    with pytest.raises(ValidationError) as excinfo:
        section.full_clean()
    assert list(excinfo.value.message_dict) == ["image_alt"]
    with pytest.raises(IntegrityError), transaction.atomic():
        section.save()


# --- Images ----------------------------------------------------------------------


@pytest.fixture
def media(settings, tmp_path) -> Path:
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def photo(kind: str = "JPEG", size: tuple[int, int] = (3000, 2000)) -> bytes:
    """A picture carrying what a phone or an editor writes into one."""
    image = Image.new("RGB", size, "teal")
    out = BytesIO()
    if kind == "PNG":
        notes = PngImagePlugin.PngInfo()
        notes.add_text("Author", "operator@example.test")
        image.save(out, "PNG", pnginfo=notes)
    elif kind in ("JPEG", "WEBP"):
        exif = Image.Exif()
        exif[ExifTags.Base.Model] = "Phone 15"
        exif[ExifTags.Base.Orientation] = 6  # held sideways: turn a quarter to view
        exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitude] = (35.0, 41.0, 0.0)
        image.save(out, kind, exif=exif, comment="operator@example.test")
    else:
        image.save(out, kind)
    return out.getvalue()


def section_with(product, name: str, data: bytes) -> ProductSection:
    section = ProductSection(product=product, kind="after_payment", image_alt="صفحه‌ی اشتراک")
    section.image = SimpleUploadedFile(name, data)
    section.full_clean()
    section.save()
    return section


@pytest.mark.parametrize("kind, suffix", [("JPEG", ".jpg"), ("WEBP", ".webp")])
def test_an_uploaded_photo_is_stored_redrawn_without_its_metadata(product, media, kind, suffix):
    """A photo's EXIF can hold where it was taken. The stored file is a new
    drawing at a capped size, and the upload itself never reaches MEDIA_ROOT,
    which the proxy serves as it is."""
    original = photo(kind)

    section = section_with(product, f"IMG_2041{suffix}", original)

    stored = Image.open(section.image.path)
    assert stored.format == kind
    assert stored.size == (1067, 1600), "turned upright by its EXIF, then capped at 1600"
    assert not stored.getexif() and "exif" not in stored.info
    assert "comment" not in stored.info
    assert "IMG_2041" not in section.image.name
    files = [path for path in media.rglob("*") if path.is_file()]
    assert files == [Path(section.image.path)]
    assert files[0].read_bytes() != original


def test_a_png_loses_its_text_chunks(product, media):
    section = section_with(product, "screenshot.png", photo("PNG", (800, 600)))

    stored = Image.open(section.image.path)
    assert stored.format == "PNG" and stored.size == (800, 600), "a small image is not enlarged"
    assert stored.text == {}


def test_a_logo_is_capped_smaller_than_a_section_image(product, media):
    product.image = SimpleUploadedFile("logo.png", photo("PNG", (2000, 2000)))
    product.save()

    assert Image.open(product.image.path).size == (512, 512)


@pytest.mark.parametrize(
    "name, data",
    [
        ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
        ("logo.gif", photo("GIF", (10, 10))),
        ("logo.png", b"not an image at all"),
    ],
)
def test_anything_but_png_jpeg_or_webp_is_refused(product, media, name, data):
    section = ProductSection(product=product, kind="after_payment", image_alt="x")
    section.image = SimpleUploadedFile(name, data)

    with pytest.raises(ValidationError) as excinfo:
        section.full_clean()

    assert excinfo.value.message_dict["image"] == [
        "این فایل سالم نیست یا فرمتش پشتیبانی نمی‌شه. یک تصویر PNG، JPEG یا WebP انتخاب کنید."
    ]
    assert not [path for path in media.rglob("*") if path.is_file()], "nothing was stored"


def test_a_logo_stored_before_the_rule_is_left_alone(product):
    """Every seeded logo is an SVG, and the format check runs on each save of
    the product form. Applied to a stored file, it would lock every such form."""
    product.image = "products/claude-pro.svg"

    product.full_clean()

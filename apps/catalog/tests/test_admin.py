"""The operator panel, which had no tests and had already broken in silence.

The thumbnail column shipped rendering `file:///C:/...` into every `<img src>`
because Unfold's header display reads an attribute called `path` and uses it
as a URL. The server returned 200 the whole time; only a browser console said
otherwise. Everything here exists to make that class of failure loud:

- a value that must *resolve* to something, not merely be set (the image URL)
- a lookup that falls through to a default instead of raising (the status
  badge, keyed off labels that a rename would quietly move)
- Persian that a Django or Unfold upgrade could revert to English
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from io import BytesIO
from typing import Any

import pytest
import time_machine
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone, translation
from PIL import Image

from apps.accounts import services
from apps.accounts.models import User
from apps.catalog.models import (
    Category,
    Plan,
    Product,
    ProductQuestion,
    ProductSection,
    ProductSpec,
)
from apps.core import ratelimit

pytestmark = pytest.mark.django_db

PERSIAN_LETTER = re.compile(r"[آ-ی]")


@pytest.fixture
def operator(client):
    staff = User.objects.create_user("operator@example.test", "x", is_staff=True)
    staff.is_superuser = True
    staff.save()
    client.force_login(staff)
    return staff


@pytest.fixture
def catalogue():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    made = {}
    for slug, status in [
        ("live", Product.Status.ACTIVE),
        ("draft", Product.Status.DRAFT),
        ("gone", Product.Status.UNAVAILABLE),
    ]:
        # Publishable as it stands, so a test of publishing tests publishing.
        product = Product.objects.create(
            category=category,
            name=slug,
            slug=slug,
            short_description="اکانت آماده‌ی اختصاصی.",
            usage_notes="موضوعیت ندارد",
            delivery_type=Product.DeliveryType.READY_ACCOUNT,
            status=status,
        )
        Plan.objects.create(
            product=product,
            title="۱ ماهه",
            cost_price=Decimal("100"),
            sale_price=Decimal("200000"),
        )
        made[slug] = product
    return made


class FormFields(HTMLParser):
    """The name/value pairs a browser would submit from one rendered form.

    Read from the page rather than built by hand, so a test posts what the
    operator's browser posts: split date inputs, the inline management forms,
    the hidden ids and ordering fields included.
    """

    def __init__(self, form_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.form_id = form_id
        self.inside = False
        self.data: dict[str, list[str]] = {}
        self.select: str | None = None
        self.options: list[tuple[str, bool]] = []
        self.textarea: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        if tag == "form":
            self.inside = a.get("id") == self.form_id
            return
        name = a.get("name", "")
        if not self.inside or "__prefix__" in name:
            return
        if tag == "input" and name and "disabled" not in a:
            kind = a.get("type", "text")
            if kind in {"submit", "button", "file", "image", "reset"}:
                return
            if kind in {"checkbox", "radio"}:
                if "checked" in a:
                    self.data.setdefault(name, []).append(a.get("value", "on"))
                return
            self.data.setdefault(name, []).append(a.get("value", ""))
        elif tag == "select" and name:
            self.select, self.options = name, []
        elif tag == "option" and self.select:
            self.options.append((a.get("value", ""), "selected" in a))
        elif tag == "textarea" and name:
            self.textarea = name
            self.data[name] = [""]

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.inside = False
        elif tag == "select" and self.select:
            chosen = [value for value, selected in self.options if selected]
            if chosen or self.options:
                self.data[self.select] = chosen or [self.options[0][0]]
            self.select = None
        elif tag == "textarea":
            self.textarea = None

    def handle_data(self, data: str) -> None:
        if self.textarea:
            self.data[self.textarea][-1] += data

    @classmethod
    def read(cls, html: str, form_id: str) -> dict[str, Any]:
        parser = cls(form_id)
        parser.feed(html)
        # A browser drops the newline Django writes after <textarea>.
        return {
            name: [value.removeprefix("\n") for value in values]
            for name, values in parser.data.items()
        }


def change_url(product: Product) -> str:
    return reverse("admin:catalog_product_change", args=[product.pk])


def form_of(client, url: str, form_id: str = "product_form") -> dict[str, Any]:
    return FormFields.read(client.get(url).content.decode(), form_id)


def plan_prefix(data: dict[str, Any], plan: Plan) -> str:
    """The inline prefix of `plan` on the product form, e.g. `plans-0`."""
    for name, values in data.items():
        if name.startswith("plans-") and name.endswith("-id") and values == [str(plan.pk)]:
            return name.removesuffix("-id")
    raise AssertionError(f"plan {plan.pk} is not on the form")


def row_of(body: str, name: str) -> str:
    """The changelist row whose first column names `name`."""
    for row in re.findall(r"<tr[^>]*>.*?</tr>", body, re.S):
        if f">{name}<" in row or f"> {name} <" in row or re.search(rf">\s*{name}\s*<", row):
            return row
    raise AssertionError(f"no row for {name}")


def test_the_product_list_renders_for_an_operator(client, operator, catalogue):
    response = client.get(reverse("admin:catalog_product_changelist"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "live" in body and "draft" in body


def test_every_status_keeps_its_own_colour(client, operator, catalogue):
    """Unfold resolves the badge with a dict lookup and falls through to
    neutral grey when it misses. The map is keyed off the enum, so a renamed
    label or a switched language cannot grey a badge.

    Each badge is read from its own row's status cell. The first version of
    this test searched the whole page for "red", which Unfold's markup contains
    fifteen times; the second took the first badge with the right word, which
    is the phone-only copy under the name. Both passed on a grey «ناموجود»."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    for slug, status, colour in [
        ("live", Product.Status.ACTIVE, "bg-green-100"),
        ("draft", Product.Status.DRAFT, "bg-blue-100"),
        ("gone", Product.Status.UNAVAILABLE, "bg-red-100"),
    ]:
        cell = re.search(
            r'<td class="field-status_label[^"]*"[^>]*>(.*?)</td>', row_of(body, slug), re.S
        )
        assert cell, f"no status cell in the row of {slug}"
        badge = re.search(rf'<span class="([^"]*)"[^>]*>\s*{status.label}\s*</span>', cell.group(1))
        assert badge, f"the status cell of {slug} does not read {status.label}"
        assert colour in badge.group(1), f"{status.label} should render {colour}"


def test_draft_is_not_amber(client, operator, catalogue):
    """ADR-0016 reserves amber for time and nothing else, and Unfold's
    `warning` variant is orange. A draft is not a clock. The only amber in
    this panel belongs to a promotion running out."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    draft_row = row_of(body, "draft")
    assert "orange" not in draft_row
    assert "panel-time" not in draft_row


def test_the_product_mark_is_served_over_http_not_from_the_disk(
    client, operator, catalogue, settings
):
    """The bug this file exists for. Unfold's header display calls the
    attribute `path` and puts it straight into an `<img src>`; handing it
    Django's ImageFieldFile renders `file:///C:/...`, which every browser
    refuses and no server-side assertion would notice."""
    product = catalogue["live"]
    product.image = "products/claude-pro.svg"
    product.save()

    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    sources = re.findall(r'<img[^>]+src="([^"]+)"', body)
    assert sources, "the list renders no images at all"
    assert not [s for s in sources if s.startswith("file:")], sources
    assert any(s.startswith(settings.MEDIA_URL) for s in sources), sources


def test_the_change_form_opens_with_its_inlines(client, operator, catalogue):
    response = client.get(change_url(catalogue["live"]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "spec-title-suggestions" in body, "the spec datalist that keeps wording consistent"
    assert "promo_price" in body, "the plan inline"


def test_the_panel_speaks_persian(client, operator, catalogue):
    """Unfold ships no Persian at all and Django's own is overridden in
    places, so both are a project asset that an upgrade could revert."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    assert "Type to search" not in body
    assert "Filters" not in body
    assert "جستجو" in body
    assert "گذرواژه" not in body, "this shop says «رمز عبور»"


def test_dates_are_jalali(client, operator, catalogue):
    """An operator who works in Mehr and Aban should not have to convert a
    Gregorian month name in their head.

    A whole date is matched, day, month and year. The first version accepted
    any month name anywhere on the page, and «دی» is inside «مدیریت», which
    every page of the panel prints."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    assert not re.search(r"اکتبر|نوامبر|سپتامبر|ژانویه", body), "a Gregorian month leaked"
    months = "فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند"
    assert re.search(rf"[۰-۹]{{1,2}} ({months}) [۰-۹]{{4}}", row_of(body, "live"))


def test_groups_are_not_in_the_panel(client, operator):
    """This project has no roles (ADR-0014). A permission screen leading
    nowhere is worse than no screen."""
    body = client.get(reverse("admin:index")).content.decode()

    assert "/admin/auth/group/" not in body


# --- What the list answers without opening a product -----------------------


def test_a_running_discount_shows_its_time_left(client, operator, catalogue):
    """The catalogue's only real clock. Before this it took opening the
    product and reading two datetime fields to learn a promotion ends
    tonight."""
    plan = catalogue["live"].plans.first()
    plan.promo_price = Decimal("150000")
    plan.promo_ends_at = timezone.now() + timedelta(hours=5)
    plan.save()

    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    assert "۰۴:" in row_of(body, "live") or "۰۵:" in row_of(body, "live")


def test_the_countdown_is_in_the_time_colour(client, operator, catalogue):
    """Amber is the one loud thing in this panel and it means time
    (ADR-0016). The countdown was a grey badge while the code and the
    commit both said amber."""
    plan = catalogue["live"].plans.first()
    plan.promo_price = Decimal("150000")
    plan.promo_ends_at = timezone.now() + timedelta(hours=5)
    plan.save()

    row = row_of(client.get(reverse("admin:catalog_product_changelist")).content.decode(), "live")

    assert re.search(r'<span class="panel-time">\s*۰[۴۵]:', row), row


def test_the_discount_column_reads_every_available_plan(client, operator, catalogue):
    """Only the cheapest plan was read, so a discount on any other plan
    showed «—»: three of the four running discounts were invisible."""
    product = catalogue["live"]
    Plan.objects.create(
        product=product,
        title="۳ ماهه",
        cost_price=Decimal("100"),
        sale_price=Decimal("500000"),
        promo_price=Decimal("450000"),
        promo_ends_at=timezone.now() + timedelta(hours=5),
    )

    row = row_of(client.get(reverse("admin:catalog_product_changelist")).content.decode(), "live")

    assert "۰۴:" in row or "۰۵:" in row, row


def test_an_unavailable_plans_discount_is_not_counted(client, operator, catalogue):
    product = catalogue["live"]
    Plan.objects.create(
        product=product,
        title="۳ ماهه",
        cost_price=Decimal("100"),
        sale_price=Decimal("500000"),
        promo_price=Decimal("450000"),
        promo_ends_at=timezone.now() + timedelta(hours=5),
        is_available=False,
    )

    row = row_of(client.get(reverse("admin:catalog_product_changelist")).content.decode(), "live")

    assert "panel-time" not in row


def test_a_product_with_no_discount_shows_no_clock(client, operator, catalogue):
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    assert "—" in row_of(body, "live")


# --- Publishing ------------------------------------------------------------


def test_publishing_writes_the_status(client, operator, catalogue):
    """A button that looks like it worked and did not is worse than no
    button."""
    draft = catalogue["draft"]

    client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "publish", "_selected_action": [str(draft.pk)]},
        follow=True,
    )
    draft.refresh_from_db()

    assert draft.status == Product.Status.ACTIVE


@pytest.mark.parametrize("surface", ["form", "action"])
def test_publication_keeps_enabled_zero_stock_choices_visible(client, operator, catalogue, surface):
    draft = catalogue["draft"]
    draft.plans.update(stock_quantity=0)
    assert not draft.publish_problems()
    if surface == "form":
        data = form_of(client, change_url(draft))
        data["status"] = [Product.Status.ACTIVE]
        response = client.post(change_url(draft), data)
        assert response.status_code == 302
    else:
        client.post(
            reverse("admin:catalog_product_changelist"),
            {"action": "publish", "_selected_action": [str(draft.pk)]},
        )
    draft.refresh_from_db()
    assert draft.status == Product.Status.ACTIVE
    assert not draft.available_plans()


def test_unpublishing_takes_it_off_the_site(client, operator, catalogue):
    live = catalogue["live"]

    client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "unpublish", "_selected_action": [str(live.pk)]},
        follow=True,
    )
    live.refresh_from_db()

    assert live.status == Product.Status.DRAFT
    assert live not in Product.objects.public()


def test_publishing_is_in_the_products_history(client, operator, catalogue):
    """The history page is where «who took this off the shop, and when» is
    answered, and a bulk action wrote nothing there."""
    draft, live = catalogue["draft"], catalogue["live"]

    for action, product in [("publish", draft), ("unpublish", live)]:
        client.post(
            reverse("admin:catalog_product_changelist"),
            {"action": action, "_selected_action": [str(product.pk)]},
            follow=True,
        )

    published = history_of(draft)
    unpublished = history_of(live)
    assert len(published) == 1 and "فعال" in published[0].get_change_message()
    assert len(unpublished) == 1 and "پیش‌نویس" in unpublished[0].get_change_message()
    assert published[0].user == operator
    page = client.get(reverse("admin:catalog_product_history", args=[draft.pk]))
    assert published[0].get_change_message() in page.content.decode()


def test_publishing_what_is_already_live_writes_nothing(client, operator, catalogue):
    live = catalogue["live"]

    client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "publish", "_selected_action": [str(live.pk)]},
        follow=True,
    )

    assert history_of(live) == []


def test_bulk_publication_rolls_back_statuses_and_history_together(
    client, operator, catalogue, monkeypatch
):
    from apps.catalog.admin import ProductAdmin

    products = [catalogue["draft"], catalogue["gone"]]
    original_statuses = {product.pk: product.status for product in products}
    original_log = ProductAdmin.log_change
    calls = []

    def failing_history(self, request, obj, message):
        original_log(self, request, obj, message)
        calls.append(obj.pk)
        if len(calls) == 2:
            raise RuntimeError("history failure")

    monkeypatch.setattr(ProductAdmin, "log_change", failing_history)
    with pytest.raises(RuntimeError, match="history failure"):
        client.post(
            reverse("admin:catalog_product_changelist"),
            {"action": "publish", "_selected_action": [str(product.pk) for product in products]},
        )
    assert (
        dict(Product.objects.filter(pk__in=original_statuses).values_list("pk", "status"))
        == original_statuses
    )
    assert all(not history_of(product) for product in products)


def history_of(obj: Any) -> list[LogEntry]:
    return list(
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk)
        ).order_by("action_time")
    )


def test_a_get_cannot_change_a_product(client, operator, catalogue):
    """Unfold renders a row action as a plain link, so the first version of
    these actions changed state on GET — an <img src> on any page the
    operator visited while signed in would have taken a product off the
    shop. Django's bulk actions POST with a CSRF token; this holds them
    there, because the convenient shape is the unsafe one."""
    live = catalogue["live"]

    for url in [
        f"/admin/catalog/product/{live.pk}/publish/",
        f"/admin/catalog/product/{live.pk}/unpublish/",
        reverse("admin:catalog_product_changelist") + "?action=unpublish",
    ]:
        client.get(url)
        live.refresh_from_db()
        assert live.status == Product.Status.ACTIVE, url


def test_an_action_needs_change_permission(client, catalogue):
    """`is_staff` is editable in this panel, so a staff account without
    change permission is a real shape. Logged in is not authorised."""
    weak = User.objects.create_user("weak@example.test", "x", is_staff=True)
    # View permission, so they genuinely reach the changelist. Without it the
    # test would pass because the page 403s, which proves nothing about the
    # action.
    weak.user_permissions.add(Permission.objects.get(codename="view_product"))
    client.force_login(weak)
    live = catalogue["live"]

    assert client.get(reverse("admin:catalog_product_changelist")).status_code == 200

    client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "unpublish", "_selected_action": [str(live.pk)]},
        follow=True,
    )
    live.refresh_from_db()

    assert live.status == Product.Status.ACTIVE


# --- The plan list ---------------------------------------------------------


def test_the_plan_list_shows_what_each_plan_earns(client, operator, catalogue):
    """`cost_price` says it is «برای حساب سود» and nothing computed that sum
    until this page. The figure is the effective price, so a running
    discount is counted rather than the list quoting a profit nobody makes."""
    plan = catalogue["live"].plans.first()

    body = client.get(reverse("admin:catalog_plan_changelist")).content.decode()

    assert "٪" in body, body[:200]
    assert plan.product.name in body


def test_a_plan_sold_below_cost_reads_as_a_loss(client, operator, catalogue):
    """A loss looked exactly like a profit: the same grey badge, the same
    figure, a minus sign the bidi algorithm moved to the far end."""
    plan = catalogue["live"].plans.first()
    plan.cost_price = Decimal("300000")
    plan.save()

    body = client.get(reverse("admin:catalog_plan_changelist")).content.decode()

    assert re.search(r'<span class="panel-loss">\s*ضرر ۱۰۰,۰۰۰ تومان', body), body


def test_a_plan_cannot_be_created_without_its_product(client, operator):
    """A plan with no product prices nothing. It is added on the product."""
    assert client.get(reverse("admin:catalog_plan_add")).status_code in (403, 302)


# --- The product form ------------------------------------------------------


def test_a_plan_rule_is_reported_under_its_own_field(client, operator, catalogue):
    """A CHECK names no field, so Django reported a promotion above the price
    above the form, and Unfold's stacked inline prints nothing there: the
    operator got an English banner and nothing below it to correct."""
    product = catalogue["live"]
    data = form_of(client, change_url(product))
    prefix = plan_prefix(data, product.plans.get())
    data[f"{prefix}-promo_price"] = ["300000"]

    response = client.post(change_url(product), data)

    assert response.status_code == 200, "the bad promotion was saved"
    body = response.content.decode()
    error = re.search(rf'<ul class="errorlist" id="id_{prefix}-promo_price_error">(.*?)</ul>', body)
    assert error, "the rule is not under the promotion field"
    assert "قیمت با تخفیف باید بیشتر از صفر و کمتر از قیمت فروش باشه." in error.group(1)


def test_a_failed_save_says_so_in_persian(client, operator, catalogue):
    product = catalogue["live"]
    data = form_of(client, change_url(product))
    data["delivery_hours"] = ["0"]

    body = client.post(change_url(product), data).content.decode()

    assert "Please correct" not in body
    assert "ذخیره نشد" in body


def test_the_search_palette_counts_in_persian(client, operator):
    """Ctrl+K's count is a plural string, which the catalogue compiler could
    not read, so every search ended in «Found 1 result in 0.01 seconds»."""
    body = client.get(reverse("admin:search"), {"s": "محصول"}).content.decode()

    assert "command-results-note" in body and "/admin/catalog/product/" in body, "a result"
    note = body[body.index('id="command-results-note"') :]
    assert "Found" not in note and "seconds" not in note
    assert re.search(r"[0-9]+</span> نتیجه", note), note[:300]


def test_the_search_palette_names_what_it_found_in_persian(client, operator, catalogue):
    """Unfold describes a found item by its app's code label, so every product
    in the Persian palette was «Catalog - محصول»."""
    body = client.get(reverse("admin:search"), {"s": "live"}).content.decode()

    assert change_url(catalogue["live"]) in body, "positive control: the product was found"
    described = re.findall(r'data-description="([^"]*)"', body)
    assert described and not [text for text in described if re.search("[A-Za-z]", text)], described


def test_saving_a_plan_below_cost_warns(client, operator, catalogue):
    product = catalogue["live"]
    data = form_of(client, change_url(product))
    prefix = plan_prefix(data, product.plans.get())
    data[f"{prefix}-cost_price"] = ["300000"]

    response = client.post(change_url(product), data, follow=True)

    warnings = [str(m) for m in response.context["messages"] if m.level_tag == "warning"]
    assert warnings and "زیر قیمت خرید" in warnings[0], warnings


def test_a_price_change_keeps_the_old_and_the_new_price(client, operator, catalogue):
    """A wrong price is found days later. Without the old figure in the
    history nobody can say what it was before, or since when."""
    product = catalogue["live"]
    plan = product.plans.get()
    data = form_of(client, change_url(product))
    data[f"{plan_prefix(data, plan)}-sale_price"] = ["250000"]

    client.post(change_url(product), data)

    for obj in (product, plan):
        messages = [entry.get_change_message() for entry in history_of(obj)]
        assert any("۲۰۰,۰۰۰" in m and "۲۵۰,۰۰۰" in m for m in messages), (obj, messages)
    # The plan comes first: after the amount, «در پلن» read as part of the price.
    [line] = [entry.get_change_message() for entry in history_of(product)]
    assert line == f"قیمت فروش از ۲۰۰,۰۰۰ تومان به ۲۵۰,۰۰۰ تومان در پلن «{plan}» تغییر کرد.", line


def test_a_history_line_separates_its_fields_like_persian(client, operator, catalogue):
    """Django's Persian joins the fields with «،» and no space after it."""
    product = catalogue["live"]
    data = form_of(client, change_url(product))
    data.update(name=["live2"], short_description=["کوتاه"], aliases=["نام دیگر"])

    client.post(change_url(product), data)

    [line] = [entry.get_change_message() for entry in history_of(product)]
    assert "، " in line and not re.search(r"،\S", line), line


def test_a_new_product_promises_no_delivery_type_until_one_is_chosen(client, operator, catalogue):
    """Unfold offers a blank only on blank=True fields, so a new product
    arrived with «اکانت آماده» already selected, a promise nobody made."""
    body = client.get(reverse("admin:catalog_product_add")).content.decode()

    select = re.search(r'<select name="delivery_type".*?</select>', body, re.S)
    assert select
    first = re.search(r"<option([^>]*)>", select.group(0))
    assert first and 'value=""' in first.group(1), first


def test_a_latin_name_fills_an_empty_slug(client, operator, catalogue):
    """The help text promised this and the form refused an empty slug."""
    data = form_of(client, reverse("admin:catalog_product_add"))
    data.update(
        name=["Spotify Premium"],
        slug=[""],
        category=[str(catalogue["live"].category_id)],
        delivery_type=[Product.DeliveryType.ON_CUSTOMER_ACCOUNT],
    )

    response = client.post(reverse("admin:catalog_product_add"), data)

    assert response.status_code == 302, response.content.decode()[:3000]
    assert Product.objects.get(name="Spotify Premium").slug == "spotify-premium"


def test_a_persian_name_needs_its_slug_written(client, operator, catalogue):
    data = form_of(client, reverse("admin:catalog_product_add"))
    data.update(
        name=["اسپاتیفای پریمیوم"],
        slug=[""],
        category=[str(catalogue["live"].category_id)],
        delivery_type=[Product.DeliveryType.ON_CUSTOMER_ACCOUNT],
    )

    response = client.post(reverse("admin:catalog_product_add"), data)

    assert response.status_code == 200
    assert 'id="id_slug_error"' in response.content.decode()
    assert not Product.objects.filter(name="اسپاتیفای پریمیوم").exists()


def test_a_persian_slug_is_refused(client, operator, catalogue):
    """A slug is the page's address. Persian in a URL is percent-encoded into
    a hundred characters of %D8 wherever it is pasted."""
    data = form_of(client, reverse("admin:catalog_product_add"))
    data.update(
        name=["Spotify Premium"],
        slug=["اسپاتیفای"],
        category=[str(catalogue["live"].category_id)],
        delivery_type=[Product.DeliveryType.ON_CUSTOMER_ACCOUNT],
    )

    response = client.post(reverse("admin:catalog_product_add"), data)

    assert response.status_code == 200
    assert not Product.objects.filter(slug="اسپاتیفای").exists()


@pytest.mark.parametrize("admin_name", ["product", "category"])
def test_a_persian_slug_saved_before_slugs_were_latin_still_saves(
    client, operator, catalogue, admin_name
):
    """Such a row could not be saved at all, its plans' prices included, until
    its slug was rewritten, and rewriting it moves an address that links on
    Telegram already point at. It stays until the operator changes it."""
    product = catalogue["live"]
    row = product if admin_name == "product" else product.category
    type(row).objects.filter(pk=row.pk).update(slug="ابزار-توسعه")
    url = reverse(f"admin:catalog_{admin_name}_change", args=[row.pk])
    data = form_of(client, url, f"{admin_name}_form")
    data["name"] = ["ویرایش‌شده"]

    response = client.post(url, data)

    assert response.status_code == 302, response.content.decode()[:3000]
    row.refresh_from_db()
    assert (row.name, row.slug) == ("ویرایش‌شده", "ابزار-توسعه")


def test_a_persian_slug_cannot_be_changed_to_another(client, operator, catalogue):
    product = catalogue["live"]
    Product.objects.filter(pk=product.pk).update(slug="ابزار-توسعه")
    data = form_of(client, change_url(product))
    data["slug"] = ["ابزار-تازه"]

    response = client.post(change_url(product), data)

    assert response.status_code == 200
    assert 'id="id_slug_error"' in response.content.decode()
    product.refresh_from_db()
    assert product.slug == "ابزار-توسعه"


def test_spec_order_is_a_field_the_form_posts(client, operator, catalogue):
    """Dragging a spec reorders the rows and writes the order into each row's
    `sort_order` input. With the field gone from the inline there was no
    input to write to, so the drag looked like it worked and saved nothing."""
    product = catalogue["live"]
    first = ProductSpec.objects.create(product=product, title="ریجن", value="آمریکا", sort_order=0)
    second = ProductSpec.objects.create(product=product, title="تحویل", value="کد", sort_order=1)
    data = form_of(client, change_url(product))
    prefix = {
        values[0]: name.removesuffix("-id")
        for name, values in data.items()
        if name.startswith("specs-") and name.endswith("-id")
    }
    data[f"{prefix[str(first.pk)]}-sort_order"] = ["1"]
    data[f"{prefix[str(second.pk)]}-sort_order"] = ["0"]
    # Unfold numbers every row of the table on a drop, the blank extra rows
    # too, and a blank row that only changed position is still blank.
    blank = sorted(
        n for n, v in data.items() if n.startswith("specs-") and n.endswith("-title") and v == [""]
    )
    for position, name in enumerate(blank, start=2):
        data[name.replace("-title", "-sort_order")] = [str(position)]

    response = client.post(change_url(product), data)

    assert response.status_code == 302, "the save failed"
    assert list(product.specs.values_list("title", flat=True)) == ["تحویل", "ریجن"]


def aria_references(body: str) -> set[str]:
    return {
        ref
        for value in re.findall(r'aria-(?:describedby|labelledby)="([^"]+)"', body)
        for ref in value.split()
    }


def test_every_aria_reference_in_the_product_form_has_a_target(client, operator, catalogue):
    """Django points each input at `<id>_helptext`, and Unfold printed the
    help without that id, so a screen reader was sent to nothing."""
    ProductSpec.objects.create(product=catalogue["live"], title="ریجن", value="آمریکا")
    body = client.get(change_url(catalogue["live"])).content.decode()

    ids = set(re.findall(r'\sid="([^"]+)"', body))
    references = aria_references(body)
    assert len(references) > 10, "positive control: the form references its help"
    assert not references - ids, sorted(references - ids)


def test_every_aria_reference_has_a_target_after_a_failed_save(client, operator, catalogue):
    """A field with an error is pointed at `<id>_error` too, and the spec
    cell printed its errors without that id."""
    product = catalogue["live"]
    ProductSpec.objects.create(product=product, title="ریجن", value="آمریکا")
    data = form_of(client, change_url(product))
    data["name"] = [""]
    data["specs-1-value"] = ["بدون عنوان"]

    body = client.post(change_url(product), data).content.decode()

    ids = set(re.findall(r'\sid="([^"]+)"', body))
    references = aria_references(body)
    errors = {"id_name_error", "id_specs-1-title_error"}
    assert errors <= references, "positive control: both errors are referenced"
    assert not references - ids, sorted(references - ids)


def test_each_discount_date_and_time_box_says_which_it_is(client, operator, catalogue):
    """The four boxes were named only by their placeholders, «تاریخ» and
    «ساعت», twice per plan: a screen reader could not tell start from end,
    and Django points no split field at its help."""
    plan = catalogue["live"].plans.get()
    pages = [
        (change_url(catalogue["live"]), "plans-0-"),
        (reverse("admin:catalog_plan_change", args=[plan.pk]), ""),
    ]
    for url, prefix in pages:
        body = client.get(url).content.decode()
        for name, label in [("promo_starts_at", "شروع تخفیف"), ("promo_ends_at", "پایان تخفیف")]:
            for part, kind in [("0", "تاریخ"), ("1", "ساعت")]:
                box = re.search(rf'<input[^>]*name="{prefix}{name}_{part}"[^>]*>', body)
                assert box, (url, name, part)
                assert f'aria-label="{kind} {label}"' in box.group(0), box.group(0)
                assert f'aria-describedby="id_{prefix}{name}_helptext"' in box.group(0)


def test_every_spec_input_has_a_label(client, operator, catalogue):
    """The specs table labels its columns in a header row that is hidden on
    a phone, and no input carried a label of its own."""
    ProductSpec.objects.create(product=catalogue["live"], title="ریجن", value="آمریکا")
    body = client.get(change_url(catalogue["live"])).content.decode()

    labelled = set(re.findall(r'<label[^>]*for="([^"]+)"', body))
    inputs = set(re.findall(r'id="(id_specs-\d+-(?:title|value|is_key))"', body))
    assert inputs, "positive control"
    assert inputs <= labelled, sorted(inputs - labelled)


# --- Search ----------------------------------------------------------------


def test_the_panel_search_reads_persian_like_the_shop(client, operator, catalogue):
    """The panel's search was Django's icontains: an Arabic-keyboard «ك», a
    missing half-space or a near spelling found nothing there while the shop
    found the product."""
    live = catalogue["live"]
    live.aliases = "کلاود"
    live.save()

    response = client.get(reverse("admin:catalog_product_changelist") + "?q=كلاود")

    assert [p.name for p in response.context["cl"].result_list] == ["live"]


def test_the_plan_search_finds_a_plan_by_its_products_spelling(client, operator, catalogue):
    live = catalogue["live"]
    live.aliases = "کلاود"
    live.save()

    response = client.get(reverse("admin:catalog_plan_changelist") + "?q=كلاود")

    assert [p.product.name for p in response.context["cl"].result_list] == ["live"]


# --- Signing in ------------------------------------------------------------

PASSWORD = "a long enough passphrase"  # noqa: S105 — a fixture value, not a credential


@pytest.fixture
def staff_account():
    return User.objects.create_user("panel@example.test", PASSWORD, is_staff=True)


def panel_login(client, password: str) -> None:
    client.post(
        reverse("admin:login"),
        {"username": "panel@example.test", "password": password, "next": "/admin/"},
    )


def test_the_panel_refuses_the_eleventh_attempt_even_with_the_right_password(client, staff_account):
    """The storefront's sign-in had a budget and the panel's had none, while
    the panel is the one that can change every price on the shop."""
    for _ in range(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL.times):
        panel_login(client, "wrong")

    response = client.post(
        reverse("admin:login"),
        {"username": "panel@example.test", "password": PASSWORD, "next": "/admin/"},
    )

    assert "_auth_user_id" not in client.session
    assert services.TOO_MANY_WRONG_PASSWORDS in response.content.decode()


def test_the_panel_refuses_a_network_that_spent_its_budget(client, staff_account):
    """Thirty tries per IP in an hour, across every address: with only the
    budget per address, one client could spread its guesses over many."""
    for n in range(ratelimit.LOGIN_ATTEMPTS_PER_IP.times):
        client.post(
            reverse("admin:login"), {"username": f"guess{n}@example.test", "password": "wrong"}
        )

    response = client.post(
        reverse("admin:login"),
        {"username": "panel@example.test", "password": PASSWORD, "next": "/admin/"},
    )

    assert "_auth_user_id" not in client.session
    assert services.TOO_MANY_FROM_NETWORK in response.content.decode()


def test_a_wrong_guess_never_buys_time_at_the_panel(client, staff_account):
    """The window opens with the first attempt and every guess inside it is
    counted against that window, never a new one. Fifteen minutes after the
    first guess the budget is whole again, however many came after it."""
    with time_machine.travel(1_790_000_000, tick=False) as clock:
        panel_login(client, "wrong")
        clock.shift(14 * 60)
        for _ in range(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL.times - 1):
            panel_login(client, "wrong")
        panel_login(client, PASSWORD)
        assert "_auth_user_id" not in client.session, "the eleventh attempt got in"

        clock.shift(61)
        panel_login(client, PASSWORD)
        assert "_auth_user_id" in client.session


def test_the_panel_counts_per_address_whatever_its_case(client, staff_account):
    for _ in range(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL.times):
        client.post(reverse("admin:login"), {"username": "PANEL@example.test", "password": "wrong"})

    panel_login(client, PASSWORD)

    assert "_auth_user_id" not in client.session


def test_the_panel_accepts_the_accounts_email_in_mixed_case(client, staff_account):
    response = client.post(
        reverse("admin:login"), {"username": "PaNel@EXAMPLE.test", "password": PASSWORD}
    )

    assert response.status_code == 302
    assert client.session.get("_auth_user_id") == str(staff_account.pk)


def test_only_a_real_sign_in_clears_the_budget(client, staff_account):
    """A post with the address and no password skips the password check. If
    it also cleared the budget, an empty submission after every nine guesses
    would make the limit endless."""
    for _ in range(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL.times):
        panel_login(client, "wrong")
    client.post(reverse("admin:login"), {"username": "panel@example.test", "password": ""})

    panel_login(client, PASSWORD)

    assert "_auth_user_id" not in client.session


def test_signing_in_at_the_panel_clears_the_budget(client, staff_account):
    panel_login(client, "wrong")
    panel_login(client, PASSWORD)

    assert ratelimit.remaining(ratelimit.LOGIN_ATTEMPTS_PER_EMAIL, "panel@example.test") == (
        ratelimit.LOGIN_ATTEMPTS_PER_EMAIL.times
    )


def test_signing_in_at_the_panel_lands_in_the_panel(client, staff_account):
    """Unfold's sign-in form dropped Django's hidden `next`, so a direct
    visit to /admin/login/ signed the operator in and sent them to the
    customer's /account/."""
    data = form_of(client, reverse("admin:login"), form_id="login-form")
    data.update(username=["panel@example.test"], password=[PASSWORD])

    response = client.post(reverse("admin:login"), data)

    assert response.status_code == 302
    assert response["Location"] == reverse("admin:index")


def test_the_sign_in_page_names_the_panel(client):
    body = client.get(reverse("admin:login")).content.decode()

    assert "ورود به پنل مدیریت" in body
    assert "خوش آمدید" not in body


def test_the_sign_in_boxes_keep_unfolds_styling(client):
    """Unfold styles the two inputs in its own login form. A limit built on
    Django's form instead rendered both boxes borderless and invisible."""
    body = client.get(reverse("admin:login")).content.decode()

    for name in ("username", "password"):
        box = re.search(rf'<input[^>]*name="{name}"[^>]*>', body)
        assert box and "border-base-200" in box.group(0), box


def test_a_signed_in_customer_is_told_in_persian_why_the_panel_refused(client):
    """Django's own Persian: «…customer@example.testوارد شده اید. ولی اجازه
    مشاهده صفحه فوق را نداریدو…»."""
    customer = User.objects.create_user("customer@example.test", PASSWORD)
    client.force_login(customer)

    body = client.post(
        reverse("admin:login"), {"username": "customer@example.test", "password": PASSWORD}
    ).content.decode()

    assert "اما این حساب به پنل دسترسی نداره. با حساب مدیر وارد بشید." in body
    for leak in ("نداریدو", "صفحه فوق", "مایلید", "authenticated"):
        assert leak not in body, leak


def test_the_spec_title_box_keeps_unfolds_styling(client, operator, catalogue):
    """The box that suggests titles was a plain TextInput, borderless and
    invisible in an empty row."""
    body = client.get(change_url(catalogue["live"])).content.decode()

    box = re.search(r'<input[^>]*name="specs-0-title"[^>]*>', body)
    assert box and "border-base-200" in box.group(0), box
    assert 'list="spec-title-suggestions"' in box.group(0)


# --- Django's and Unfold's Persian -----------------------------------------


def test_the_password_change_errors_say_ramz_obour(client, operator):
    response = client.post(
        reverse("admin:password_change"),
        {"old_password": "wrong", "new_password1": "abcdefgh1", "new_password2": "abcdefgh2"},
    )

    body = response.content.decode()
    assert "رمز عبور فعلی اشتباه است." in body
    assert "تکرار رمز عبور یکسان نیست." in body
    for leak in ("گذرواژه", "لطفاً", "Please correct"):
        assert leak not in body, leak


def test_the_delete_confirmation_reads_as_persian(client, operator, catalogue):
    body = client.get(
        reverse("admin:catalog_product_delete", args=[catalogue["live"].pk])
    ).content.decode()

    assert "آیا مطمئنید" not in body
    for leak in ("اشیا", "·", "کلیهٔ", "خواهند شد"):
        assert leak not in body, leak
    assert re.search(r"محصولات: ۱\b", body)


def test_the_bulk_delete_confirmation_reads_as_persian(client, operator, catalogue):
    body = client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "delete_selected", "_selected_action": [str(catalogue["live"].pk)]},
    ).content.decode()

    for leak in ("موجودیت", "ذیل", "در خصوص", "اشیا"):
        assert leak not in body, leak
    # One product is named in the singular, and a plural verb did not agree.
    assert "محصول انتخاب‌شده رو حذف می‌کنید؟" in body
    assert "حذف شوند؟" not in body


def test_the_actions_menu_reads_as_persian(client, operator, catalogue):
    """Django's fa says «حذف  محصولات های انتخاب شده»: two spaces and a
    plural on a plural."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    options = re.findall(r'<option value="delete_selected"[^>]*>([^<]*)</option>', body)
    assert options == ["حذف محصولات انتخاب‌شده"], options


def test_unfolds_own_strings_are_translated(client, operator, catalogue):
    body = client.get(change_url(catalogue["live"])).content.decode()

    for leak in ("Select value", "Choose file to upload"):
        assert leak not in body, leak


def test_a_plans_product_is_labelled_in_persian(client, operator, catalogue):
    """Django builds this label from the field name, so no translation can
    reach it; only a verbose_name can."""
    plan = catalogue["live"].plans.get()
    body = client.get(reverse("admin:catalog_plan_change", args=[plan.pk])).content.decode()

    labels = re.findall(r"<label[^>]*>(.*?)</label>", body, re.S)
    assert not [label for label in labels if "Product" in label], labels
    assert any("محصول" in label for label in labels)


def test_the_history_page_is_jalali(client, operator, catalogue):
    draft = catalogue["draft"]
    client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "publish", "_selected_action": [str(draft.pk)]},
    )

    body = client.get(reverse("admin:catalog_product_history", args=[draft.pk])).content.decode()

    months = "فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند"
    assert re.search(rf"[۰-۹]{{1,2}} ({months}) [۰-۹]{{4}}", body)
    assert not re.search(r"\b20[0-9]{2}\b", body), "a Gregorian year"
    assert "entries" not in body and "entry" not in body


def test_the_dashboards_recent_actions_are_jalali(client, operator, catalogue):
    """«کارهای اخیر» printed Django's own date: «20 اکتبر 2026، ساعت 2:32»."""
    LogEntry.objects.log_actions(
        user_id=operator.pk,
        queryset=[catalogue["live"]],
        action_flag=CHANGE,
        change_message="",
        single_object=True,
    )

    body = client.get(reverse("admin:index")).content.decode()

    assert "recent-actions-module" in body, "positive control: the list is on the page"
    assert not re.search(r"اکتبر|نوامبر|سپتامبر|ژانویه|\b20[0-9]{2}\b", body)
    months = "فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند"
    assert re.search(rf"[۰-۹]{{1,2}} ({months}) [۰-۹]{{4}}، [۰-۹]{{2}}:[۰-۹]{{2}}", body)


def test_a_customers_dates_are_jalali(client, operator):
    customer = User.objects.create_user("customer@example.test")
    customer.last_login = timezone.now()
    customer.save()

    body = client.get(reverse("admin:accounts_user_change", args=[customer.pk])).content.decode()

    gregorian = "ژانویه|فوریه|مارس|آوریل|ژوئن|ژوئیه|اوت|سپتامبر|اکتبر|نوامبر|دسامبر"
    assert not re.search(gregorian, body), "a Gregorian month on the customer's page"
    months = "فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند"
    assert len(re.findall(rf"[۰-۹]{{1,2}} ({months}) [۰-۹]{{4}}", body)) == 2


def test_the_breadcrumb_keeps_data_as_it_was_written(client, operator):
    """Unfold capitalised every part of the path, so a customer's address
    read «Customer@example.test» and iCloud read «ICloud»."""
    customer = User.objects.create_user("customer@example.test")

    body = client.get(reverse("admin:accounts_user_change", args=[customer.pk])).content.decode()

    assert "Customer@example.test" not in body
    assert "<bdi>customer@example.test</bdi>" in body


def test_a_latin_name_in_the_breadcrumb_is_cut_at_its_end(client, operator, catalogue):
    """The crumb took the page's right-to-left direction, so on a phone the
    ellipsis ate a Latin name's start and both gift cards read «…Gift Card»."""
    live = catalogue["live"]
    Product.objects.filter(pk=live.pk).update(name="Google Play Gift Card")

    body = client.get(change_url(live)).content.decode()

    [crumb] = re.findall(r"<(?:a|span) ([^>]*)>\s*<bdi>Google Play Gift Card</bdi>", body)
    assert "truncate" in crumb and 'dir="auto"' in crumb, crumb


def test_the_paths_arrows_point_the_way_a_persian_reader_moves(client, operator, catalogue):
    body = client.get(change_url(catalogue["live"])).content.decode()

    assert "chevron_right" not in body and "chevron_left" in body
    back = re.search(r'title="بازگشت"[^>]*>\s*<span[^>]*>(\w+)</span>', body)
    assert back and back.group(1) == "arrow_forward", back


# --- The English side of the switch ----------------------------------------


@pytest.fixture
def english(client, operator):
    client.post(reverse("set_language"), {"language": "en", "next": "/admin/"})
    yield
    client.post(reverse("set_language"), {"language": "fa", "next": "/admin/"})


def chrome(body: str) -> list[str]:
    """The words the panel writes around the data on a page: field labels,
    help, section headings, column headings, buttons and badges.

    Read from the content area only; the sidebar and the path above it have
    their own test. Product and category names are data and stay as the
    operator wrote them.
    """
    content = body[body.index('id="content"') :]
    found = [
        inner
        for tag in ("label", "h2", "button", "thead")
        for inner in re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", content, re.S)
    ]
    found += re.findall(
        r'<div class="leading-relaxed mt-2 text-xs"[^>]*>(.*?)</div>', content, re.S
    )
    # Material Symbols are ligatures, so an icon reads as a word: dropped.
    text = [
        re.sub(r"<[^>]+>", " ", re.sub(r'<span class="material-symbols[^>]*>[^<]*</span>', "", s))
        for s in found
    ]
    return [" ".join(t.split()) for t in text if t.strip()]


def test_the_english_product_form_is_english(client, english, catalogue):
    """A Persian panel switched to English used to keep every label and help
    text in Persian, with «Delete محصول» on the button."""
    body = client.get(change_url(catalogue["live"])).content.decode()

    assert 'dir="ltr"' in body
    words = chrome(body)
    assert len(words) > 20, "positive control: the form's labels were read"
    persian = [text for text in words if PERSIAN_LETTER.search(text)]
    # The help text for aliases and the customer-input label quotes the
    # Persian a customer types and the sentence the shop prints, on purpose.
    quoted = ("چت جی‌پی‌تی", "را می‌پرسیم", "تا … ساعت")
    assert not [text for text in persian if not any(q in text for q in quoted)], persian
    assert "Delete product" in body


def test_the_english_sidebar_and_path_are_english(client, english):
    """The sidebar's sections are written in settings and the path's first
    step is the app's name. Both were plain strings the switch never reached."""
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    start = body.index('id="nav-sidebar-apps"')
    sidebar = body[start : body.index("</nav>", start)]
    # Material Symbols icons are ligature names, not words.
    words = [
        word
        for word in re.findall(r"<(?:h2|span)\b[^>]*>\s*([^<]+?)\s*<", sidebar)
        if not re.fullmatch(r"[a-z0-9_]+", word)
    ]
    assert {"Shop", "Products", "Plans and prices", "Categories", "Customers", "Users"} <= set(
        words
    ), words
    assert not [word for word in words if PERSIAN_LETTER.search(word)], words
    assert "Store management" in body
    path = re.search(r'<a href="/admin/catalog/"[^>]*>\s*(.*?)\s*</a\s*>', body, re.S)
    assert path and path.group(1) == "Shop", path


def test_the_panels_language_stays_in_the_panel(client, english, catalogue):
    """The shop has one language. A panel switched to English printed the
    product page's delivery type and warranty in English too."""
    product = catalogue["live"]
    with translation.override("fa"):
        persian = str(product.get_delivery_type_display())
    with translation.override("en"):
        english_label = str(product.get_delivery_type_display())

    shop = client.get(reverse("product", args=[product.slug])).content.decode()
    panel = client.get(change_url(product)).content.decode()

    assert english_label != persian and english_label in panel, "positive control"
    assert persian in shop and english_label not in shop


def test_the_english_product_list_is_english(client, english, catalogue):
    body = client.get(reverse("admin:catalog_product_changelist")).content.decode()

    words = chrome(body)
    assert any("Status" in text for text in words), words
    assert not [text for text in words if PERSIAN_LETTER.search(text)], words
    options = re.findall(r'<select name="action".*?</select>', body, re.S)[0]
    assert not PERSIAN_LETTER.search(re.sub(r"<[^>]+>", "", options)), options
    badges = re.findall(r'<span class="[^"]*rounded-default[^"]*"[^>]*>\s*([^<]+?)\s*</span>', body)
    assert {"Active", "Draft", "Unavailable"} <= set(badges), badges


# --- The content model on the product form -----------------------------------


def add_row(data: dict[str, Any], prefix: str, **fields: str) -> str:
    """Add a row to inline `prefix` the way «افزودن» does, and return its prefix."""
    fields.setdefault("sort_order", "0")
    n = int(data[f"{prefix}-TOTAL_FORMS"][0])
    data[f"{prefix}-TOTAL_FORMS"] = [str(n + 1)]
    data.update({f"{prefix}-{n}-{name}": [value] for name, value in fields.items()})
    return f"{prefix}-{n}"


def errors_under(body: str, field_id: str) -> str:
    found = re.search(rf'<ul class="errorlist" id="id_{field_id}_error">(.*?)</ul>', body, re.S)
    return found.group(1) if found else ""


def test_the_form_refuses_to_publish_without_usage_notes(client, operator, catalogue):
    draft = catalogue["draft"]
    data = form_of(client, change_url(draft))
    data.update(status=["active"], usage_notes=[""])

    response = client.post(change_url(draft), data)

    assert response.status_code == 200, "a product with no usage notes was published"
    body = response.content.decode()
    assert "نکات استفاده رو بنویسید." in errors_under(body, "usage_notes")
    assert "موارد مشخص‌شده توی فرم رو کامل کنید" in errors_under(body, "status")
    draft.refresh_from_db()
    assert draft.status == Product.Status.DRAFT
    # The form holds the refused «فعال»; the page it links to is still a preview.
    assert page_link(body, "پیش‌نمایش نسخه‌ی ذخیره‌شده")


def test_not_applicable_publishes_from_the_form(client, operator, catalogue):
    """The control for the refusal above, on the same draft."""
    draft = catalogue["draft"]
    data = form_of(client, change_url(draft))
    data.update(status=["active"], usage_notes=["موضوعیت ندارد"])

    response = client.post(change_url(draft), data)

    assert response.status_code == 302, response.content.decode()[:3000]
    draft.refresh_from_db()
    assert draft.status == Product.Status.ACTIVE


def test_the_form_refuses_to_publish_with_no_plan_on_sale(client, operator, catalogue):
    """Read from the rows as submitted: the plan was on sale until this save."""
    draft = catalogue["draft"]
    data = form_of(client, change_url(draft))
    data.pop(f"{plan_prefix(data, draft.plans.get())}-is_available")
    data["status"] = ["active"]

    response = client.post(change_url(draft), data)

    assert response.status_code == 200
    group = response.content.decode().split('id="plans-group"', 1)[1]
    assert "برای انتشار، حداقل یک پلن با وضعیت «موجود» لازمه." in group
    draft.refresh_from_db()
    assert draft.status == Product.Status.DRAFT


def test_a_placeholder_in_a_new_section_keeps_a_live_product_as_it_was(client, operator, catalogue):
    live = catalogue["live"]
    data = form_of(client, change_url(live))
    row = add_row(data, "sections", kind="before_buying", body="ظرف [تصمیم مالک، تصمیم ۴: …] روز")

    response = client.post(change_url(live), data)

    assert response.status_code == 200, "a live page took an unfilled placeholder"
    assert "رو کامل کنید" in errors_under(response.content.decode(), f"{row}-body")
    assert not live.sections.exists()


def test_the_plans_own_form_holds_a_live_plan_to_its_placeholders(client, operator, catalogue):
    """The rule lived in the product form's inlines only, and the plan list's
    own form put an owner placeholder into a live buy box."""
    plan = catalogue["live"].plans.get()
    url = reverse("admin:catalog_plan_change", args=[plan.pk])
    data = form_of(client, url, "plan_form")
    data["title"] = ["[تصمیم مالک، تصمیم ۴: چند روز]"]

    response = client.post(url, data)

    assert response.status_code == 200, "a live plan took an unfilled placeholder"
    assert "رو کامل کنید" in errors_under(response.content.decode(), "title")
    plan.refresh_from_db()
    assert plan.title == "۱ ماهه"


def test_publishing_from_the_form_reads_the_rows_it_does_not_show(client, catalogue):
    """Without the section permissions the form has no section rows, so no
    formset asked about the stored ones before the draft went live."""
    editor = User.objects.create_user("editor@example.test", "x", is_staff=True)
    editor.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="catalog",
            content_type__model__in=["product", "plan", "productspec"],
        )
    )
    client.force_login(editor)
    draft = catalogue["draft"]
    ProductSection.objects.create(product=draft, kind="before_buying", body="[تصمیم مالک، تصمیم ۴]")
    data = form_of(client, change_url(draft))
    assert "sections-TOTAL_FORMS" not in data, "the form shows the sections after all"
    data["status"] = ["active"]

    response = client.post(change_url(draft), data)

    assert response.status_code == 200, "a stored placeholder went live"
    assert "رو کامل کنید" in str(response.context["adminform"].form.non_field_errors())
    draft.refresh_from_db()
    assert draft.status == Product.Status.DRAFT


def test_a_draft_keeps_its_placeholders(client, operator, catalogue):
    """A placeholder is how a draft waits for the owner's decision."""
    draft = catalogue["draft"]
    data = form_of(client, change_url(draft))
    add_row(data, "sections", kind="before_buying", body="[تصمیم مالک، تصمیم ۴: …]")
    add_row(data, "questions", question="کد کجا میاد؟", answer="[از تحویل آزمایشی: …]")

    response = client.post(change_url(draft), data)

    assert response.status_code == 302, response.content.decode()[:3000]
    assert draft.sections.get().kind == ProductSection.Kind.BEFORE_BUYING
    assert draft.questions.get().question == "کد کجا میاد؟"


def test_a_stale_fact_does_not_lock_a_live_products_other_edits(client, operator, catalogue):
    """Only placeholders hold a live product's form. A check that went stale
    is the freshness list's business, not a reason to refuse a typo fix."""
    live = catalogue["live"]
    Product.objects.filter(pk=live.pk).update(usage_notes="", time_sensitive=True)
    data = form_of(client, change_url(live))
    data["name"] = ["live, renamed"]

    response = client.post(change_url(live), data)

    assert response.status_code == 302, response.content.decode()[:3000]


def test_two_before_buying_sections_in_one_save_are_refused(client, operator, catalogue):
    """The database refuses the second, but only as an IntegrityError: a crash
    in place of a message, when both rows are new."""
    draft = catalogue["draft"]
    data = form_of(client, change_url(draft))
    add_row(data, "sections", kind="before_buying", body="یک")
    add_row(data, "sections", kind="before_buying", body="دو")

    response = client.post(change_url(draft), data)

    assert response.status_code == 200
    assert "هر محصول فقط یک بخش «قبل از خرید» می‌تونه داشته باشه." in response.content.decode()
    assert not draft.sections.exists()


def test_the_bulk_action_names_every_reason_and_publishes_the_rest(client, operator, catalogue):
    draft = catalogue["draft"]
    Product.objects.filter(pk=draft.pk).update(short_description="", usage_notes="")
    ProductQuestion.objects.create(product=draft, question="پول برمی‌گرده؟", answer="[تصمیم مالک]")
    ready = Product.objects.create(
        category=draft.category,
        name="ready",
        slug="ready",
        short_description="آماده.",
        usage_notes="موضوعیت ندارد",
        delivery_type=Product.DeliveryType.GIFT_CARD,
    )
    Plan.objects.create(product=ready, title="۱۰ دلاری", cost_price=1, sale_price=500000)

    response = client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "publish", "_selected_action": [str(draft.pk), str(ready.pk)]},
        follow=True,
    )

    [error] = [str(m) for m in response.context["messages"] if m.level_tag == "error"]
    for reason in ("معرفی کوتاه محصول رو بنویسید.", "نکات استفاده", "پاسخ: جای خالی"):
        assert reason in error, error
    draft.refresh_from_db()
    ready.refresh_from_db()
    assert (draft.status, ready.status) == (Product.Status.DRAFT, Product.Status.ACTIVE)


def test_each_reason_a_bulk_publish_gives_is_a_line_of_its_own(client, operator, catalogue):
    """Joined by spaces, ChatGPT Plus's reasons were one red paragraph twelve
    lines tall on a phone."""
    draft = catalogue["draft"]
    Product.objects.filter(pk=draft.pk).update(short_description="", usage_notes="")

    response = client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "publish", "_selected_action": [str(draft.pk)]},
        follow=True,
    )

    [error] = [str(m) for m in response.context["messages"] if m.level_tag == "error"]
    headline, _, rest = error.partition('<ul class="panel-reasons">')
    assert headline == "«\u2068draft\u2069» منتشر نشد.", error
    reasons = re.findall(r"<li>(.*?)</li>", rest)
    assert "معرفی کوتاه محصول رو بنویسید." in reasons
    assert any(r.startswith("نکات استفاده رو بنویسید.") for r in reasons), reasons
    assert "&lt;li&gt;" not in response.content.decode()


def test_a_refused_publish_lists_its_reasons_at_the_top(client, operator, catalogue):
    """The reasons stood under their fields, thousands of pixels apart on a
    phone, and the list that names them was hidden on a submitted form."""
    draft = catalogue["draft"]
    data = form_of(client, change_url(draft))
    data.update(status=["active"], usage_notes=[""], short_description=[""])
    add_row(data, "questions", question="پول برمی‌گرده؟", answer="[تصمیم مالک، تصمیم ۴]")

    body = client.post(change_url(draft), data).content.decode()

    readiness = re.search(r'<div class="panel-readiness.*?</div>', body, re.S)
    assert readiness, "no list at the top of the refused form"
    assert body.index("panel-readiness") < body.index('name="usage_notes"')
    items = re.findall(r"<li>(.*?)</li>", readiness.group(0), re.S)
    for reason in ("معرفی کوتاه محصول رو بنویسید.", "نکات استفاده", "پاسخ: جای خالی"):
        assert any(reason in item for item in items), (reason, items)
    assert not any(
        "موارد مشخص‌شده توی فرم رو کامل کنید" in item for item in items
    ), "the summary is not a reason"


def test_a_bulk_publish_that_publishes_nothing_reports_no_success(client, operator, catalogue):
    """Every selected product refused, and a green «۰ محصول فعال شد.» stood
    under the red reasons as if something had gone through."""
    draft = catalogue["draft"]
    Product.objects.filter(pk=draft.pk).update(usage_notes="")

    response = client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "publish", "_selected_action": [str(draft.pk)]},
        follow=True,
    )

    levels = [m.level_tag for m in response.context["messages"]]
    assert levels == ["error"], levels


def test_a_drafts_form_lists_what_it_still_lacks(client, operator, catalogue):
    draft = catalogue["draft"]
    Product.objects.filter(pk=draft.pk).update(usage_notes="")

    body = client.get(change_url(draft)).content.decode()

    readiness = re.search(r'<div class="panel-readiness.*?</div>', body, re.S)
    assert readiness, "no readiness list on the draft's form"
    assert "قبل از انتشار" in readiness.group(0)
    assert "نکات استفاده رو بنویسید." in readiness.group(0)


def test_a_live_product_that_could_not_go_back_up_says_so_on_its_form(client, operator, catalogue):
    """The owner's live products predate the notes publishing asks for. Taken
    down, each was refused on the way back up, and nothing had said so beforehand."""
    live = catalogue["live"]
    Product.objects.filter(pk=live.pk).update(usage_notes="")

    body = client.get(change_url(live)).content.decode()

    readiness = re.search(r'<div class="panel-readiness.*?</div>', body, re.S)
    assert readiness, "no readiness list on the live product's form"
    assert "برای انتشار دوباره، این موارد رو اصلاح کنید" in readiness.group(0)
    assert "قبل از انتشار" not in readiness.group(0)
    assert "نکات استفاده رو بنویسید." in readiness.group(0)


def test_taking_down_a_product_that_could_not_go_back_up_says_why(client, operator, catalogue):
    live, gone = catalogue["live"], catalogue["gone"]
    Product.objects.filter(pk=live.pk).update(usage_notes="")

    response = client.post(
        reverse("admin:catalog_product_changelist"),
        {"action": "unpublish", "_selected_action": [str(live.pk), str(gone.pk)]},
        follow=True,
    )

    live.refresh_from_db()
    assert live.status == Product.Status.DRAFT
    # Blue like the same list on the form: orange is too close to the amber kept for time.
    [note] = [str(m) for m in response.context["messages"] if m.level_tag == "info"]
    headline, _, rest = note.partition('<ul class="panel-reasons">')
    assert headline == "برای انتشار دوباره‌ی «⁨live⁩»، این موارد رو اصلاح کنید.", note
    assert re.findall(r"<li>(.*?)</li>", rest) == [
        "نکات استفاده رو بنویسید. اگه نکته‌ای نداره، عبارت «موضوعیت ندارد» رو وارد کنید."
    ]


def test_a_ready_draft_says_so_and_a_live_product_says_nothing(client, operator, catalogue):
    assert (
        "محصول برای انتشار آماده‌ست." in client.get(change_url(catalogue["draft"])).content.decode()
    )
    assert "panel-readiness" not in client.get(change_url(catalogue["live"])).content.decode()


def page_link(body: str, label: str) -> str:
    """The href of the link on `body` whose text ends with `label`."""
    for href, text in re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
        if re.sub(r"<[^>]+>|\s+", " ", text).strip().endswith(label):
            return href
    raise AssertionError(f"no link reads {label}")


@pytest.mark.parametrize(
    "slug, label", [("draft", "پیش‌نمایش نسخه‌ی ذخیره‌شده"), ("live", "صفحه‌ی ذخیره‌شده در سایت")]
)
def test_the_product_form_links_to_the_page(client, operator, catalogue, slug, label):
    """A draft's address shows staff a preview and everyone else a 404."""
    body = client.get(change_url(catalogue[slug])).content.decode()

    response = client.get(page_link(body, label))

    assert response.status_code == 302
    assert response["Location"].endswith(f"/p/{slug}/")
    assert client.get(response["Location"]).status_code == 200


def test_an_unavailable_product_offers_no_page_to_open(client, operator, catalogue):
    """«ناموجود» is off the shop for staff as well, so a link to it opened a 404."""
    body = client.get(change_url(catalogue["gone"])).content.decode()

    for label in ("پیش‌نمایش نسخه‌ی ذخیره‌شده", "صفحه‌ی ذخیره‌شده در سایت"):
        with pytest.raises(AssertionError, match="no link reads"):
            page_link(body, label)


def copy_from(client, source: Product | None, *targets: Product, follow: bool = False):
    data: dict[str, Any] = {
        "action": "copy_sections",
        "_selected_action": [str(target.pk) for target in targets],
    }
    if source is not None:
        data["source"] = str(source.pk)
    return client.post(reverse("admin:catalog_product_changelist"), data, follow=follow)


def test_copying_sections_makes_rows_of_their_own(client, operator, catalogue):
    source, target = catalogue["live"], catalogue["draft"]
    ProductSection.objects.create(product=source, kind="benefits", body="- یک", sort_order=0)
    ProductSection.objects.create(product=source, kind="before_buying", body="شرط", sort_order=1)
    ProductSection.objects.create(product=target, kind="free", body="از قبل", sort_order=5)

    question = copy_from(client, None, target)

    assert question.status_code == 200, "the action copied before asking from where"
    assert '<select name="source"' in question.content.decode()
    assert target.sections.count() == 1

    copy_from(client, source, target, follow=True)

    copies = list(target.sections.all())
    assert [(s.kind, s.body) for s in copies] == [
        ("free", "از قبل"),
        ("benefits", "- یک"),
        ("before_buying", "شرط"),
    ]
    copies[1].body = "- دو"
    copies[1].save()
    assert source.sections.get(kind="benefits").body == "- یک", "the copy edited its original"
    [entry] = history_of(target)
    assert "بخش محتوا" in entry.get_change_message()


def test_a_copy_never_gives_a_product_a_second_before_buying(client, operator, catalogue):
    source, target = catalogue["live"], catalogue["draft"]
    ProductSection.objects.create(product=source, kind="before_buying", body="منبع")
    ProductSection.objects.create(product=target, kind="before_buying", body="خودش")

    response = copy_from(client, source, target, follow=True)

    assert list(target.sections.values_list("body", flat=True)) == ["خودش"]
    assert any("کپی نشد" in str(m) for m in response.context["messages"])


def test_section_copies_and_history_roll_back_across_all_targets(
    client, operator, catalogue, monkeypatch
):
    from apps.catalog.admin import ProductAdmin

    source, first, second = catalogue["live"], catalogue["draft"], catalogue["gone"]
    ProductSection.objects.create(product=source, kind="benefits", body="متن نمونه")
    original = ProductAdmin.log_change
    writes = []
    requests = []

    def fail_second(self, request, obj, message):
        requests.append(request)
        writes.append(obj.pk)
        if len(writes) == 2:
            raise RuntimeError("Synthetic history failure")
        return original(self, request, obj, message)

    monkeypatch.setattr(ProductAdmin, "log_change", fail_second)
    with pytest.raises(RuntimeError, match="Synthetic history failure"):
        copy_from(client, source, first, second)

    assert len(writes) == 2
    assert not first.sections.exists() and not second.sections.exists()
    assert not history_of(first) and not history_of(second)
    from django.contrib.messages import SUCCESS, get_messages

    assert not [message for message in get_messages(requests[0]) if message.level == SUCCESS]


def test_sections_are_never_copied_onto_a_live_page(client, operator, catalogue):
    """A copy writes rows straight into its target, past the publishing rule.
    Onto a live product it printed another product's claims, and the
    placeholders a draft waits on, on a page that takes payment."""
    draft, live = catalogue["draft"], catalogue["live"]
    ProductSection.objects.create(
        product=draft, kind="before_buying", body="ظرف [تصمیم مالک، تصمیم ۴: …] روز"
    )

    response = copy_from(client, draft, live, follow=True)

    assert not live.sections.exists()
    [refused] = [str(m) for m in response.context["messages"] if m.level_tag == "error"]
    assert "از سایت بردارید" in refused, refused
    client.logout()
    assert "تصمیم مالک" not in client.get(live.get_absolute_url()).content.decode()


def test_copying_sections_needs_permission_to_add_them(client, catalogue):
    clerk = User.objects.create_user("clerk@example.test", "x", is_staff=True)
    clerk.user_permissions.add(
        *Permission.objects.filter(codename__in=["view_product", "change_product"])
    )
    client.force_login(clerk)
    ProductSection.objects.create(product=catalogue["live"], kind="free", body="متن")

    copy_from(client, catalogue["live"], catalogue["draft"], follow=True)

    assert not catalogue["draft"].sections.exists()


def test_a_row_added_on_the_form_goes_last(client, operator, catalogue):
    """A new row cannot be dragged until it is saved, so it arrived with the
    default order and stood above the rows a copy had numbered from one."""
    source, draft = catalogue["live"], catalogue["draft"]
    ProductSection.objects.create(product=source, kind="benefits", body="- یک")
    ProductSection.objects.create(product=source, kind="includes", body="- دو")
    copy_from(client, source, draft, follow=True)
    data = form_of(client, change_url(draft))
    add_row(data, "sections", kind="free", body="آخر")

    response = client.post(change_url(draft), data)

    assert response.status_code == 302, response.content.decode()[:3000]
    assert list(draft.sections.values_list("kind", flat=True)) == ["benefits", "includes", "free"]


def test_only_a_product_with_sections_is_offered_to_copy_from(client, operator, catalogue):
    ProductSection.objects.create(product=catalogue["live"], kind="free", body="متن")

    body = copy_from(client, None, catalogue["draft"]).content.decode()

    select = re.search(r'<select name="source".*?</select>', body, re.S)
    assert select and ">live<" in select.group(0) and ">gone<" not in select.group(0)


# --- How fresh the prices and the claims are ------------------------------------


def test_the_plan_list_finds_prices_not_confirmed_this_week(client, operator, catalogue):
    now = timezone.now()
    for slug, when in [("live", now - timedelta(days=8)), ("draft", None), ("gone", now)]:
        Plan.objects.filter(product__slug=slug).update(price_confirmed_at=when)

    response = client.get(reverse("admin:catalog_plan_changelist") + "?price_checked=stale")

    assert {p.product.slug for p in response.context["cl"].result_list} == {"live", "draft"}


def test_the_product_list_finds_time_sensitive_claims_unchecked_for_a_month(
    client, operator, catalogue
):
    today = timezone.localdate()
    Product.objects.filter(slug="live").update(
        time_sensitive=True, facts_checked_on=today - timedelta(days=31)
    )
    Product.objects.filter(slug="draft").update(
        time_sensitive=True, facts_checked_on=today - timedelta(days=29)
    )

    response = client.get(reverse("admin:catalog_product_changelist") + "?facts=stale")

    assert [p.slug for p in response.context["cl"].result_list] == ["live"]


def test_the_product_list_finds_live_pages_with_no_usage_notes(client, operator, catalogue):
    """Every product that went live before publishing asked for the stance
    has none, and the readiness list that would name it is on drafts only."""
    Product.objects.filter(slug__in=["live", "draft"]).update(usage_notes="")

    response = client.get(reverse("admin:catalog_product_changelist") + "?facts=no_notes")

    assert [p.slug for p in response.context["cl"].result_list] == ["live"]


def test_confirming_prices_dates_only_the_selected_plans(client, operator, catalogue):
    Plan.objects.update(price_confirmed_at=None)
    chosen = catalogue["live"].plans.get()

    client.post(
        reverse("admin:catalog_plan_changelist"),
        {"action": "confirm_prices", "_selected_action": [str(chosen.pk)]},
    )

    confirmed = Plan.objects.exclude(price_confirmed_at=None)
    assert list(confirmed.values_list("pk", flat=True)) == [chosen.pk]


def test_saving_a_price_in_the_form_confirms_it(client, operator, catalogue):
    live = catalogue["live"]
    plan = live.plans.get()
    Plan.objects.filter(pk=plan.pk).update(price_confirmed_at=None)
    data = form_of(client, change_url(live))
    data[f"{plan_prefix(data, plan)}-sale_price"] = ["250000"]

    client.post(change_url(live), data)

    plan.refresh_from_db()
    assert plan.price_confirmed_at is not None


def test_both_freshness_dates_are_jalali_in_their_lists(client, operator, catalogue):
    Product.objects.filter(slug="live").update(facts_checked_on=timezone.localdate())
    months = "فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند"
    day = rf'<span class="panel-date">[۰-۹]{{1,2}} ({months}) [۰-۹]{{4}}</span>'

    products = client.get(reverse("admin:catalog_product_changelist")).content.decode()
    plans = client.get(reverse("admin:catalog_plan_changelist")).content.decode()

    assert re.search(
        r'<td class="field-facts_checked[^"]*"[^>]*>\s*' + day, row_of(products, "live")
    )
    assert re.search(r'<td class="field-price_checked[^"]*"[^>]*>\s*' + day, plans)


# --- Images from the panel -------------------------------------------------------


def png(size: tuple[int, int]) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, "teal").save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def test_a_logo_uploaded_in_the_panel_is_stored_redrawn(client, operator, catalogue, media):
    live = catalogue["live"]
    data = form_of(client, change_url(live))
    data["image"] = SimpleUploadedFile("logo.png", png((1200, 1200)), content_type="image/png")

    response = client.post(change_url(live), data)

    assert response.status_code == 302, response.content.decode()[:3000]
    live.refresh_from_db()
    assert Image.open(live.image.path).size == (512, 512)


def test_an_svg_logo_is_refused_in_persian(client, operator, catalogue, media):
    """SVG can carry script, and a logo is served from the shop's own origin."""
    live = catalogue["live"]
    data = form_of(client, change_url(live))
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    data["image"] = SimpleUploadedFile("logo.svg", svg, content_type="image/svg+xml")

    response = client.post(change_url(live), data)

    assert response.status_code == 200
    error = errors_under(response.content.decode(), "image")
    # Every logo the shop has is an SVG: the message names the formats, not "no image".
    assert (
        "این فایل سالم نیست یا فرمتش پشتیبانی نمی‌شه. یک تصویر PNG، JPEG یا WebP انتخاب کنید."
        in error
    ), error
    assert not [path for path in media.rglob("*") if path.is_file()]


def test_a_photo_cut_off_in_transit_is_refused_in_persian(client, operator, catalogue, media):
    """Pillow reads a JPEG's header without its body, so half an upload passed
    the check and failed while it was redrawn: a server error, not a message."""
    out = BytesIO()
    Image.effect_noise((800, 600), 64).convert("RGB").save(out, "JPEG")
    half = out.getvalue()[: len(out.getvalue()) // 2]
    live = catalogue["live"]
    data = form_of(client, change_url(live))
    data["image"] = SimpleUploadedFile("logo.jpg", half, content_type="image/jpeg")

    response = client.post(change_url(live), data)

    assert response.status_code == 200
    assert "این فایل سالم نیست" in errors_under(response.content.decode(), "image")
    assert not [path for path in media.rglob("*") if path.is_file()]


def test_a_section_image_without_its_description_is_refused(client, operator, catalogue, media):
    draft = catalogue["draft"]
    data = form_of(client, change_url(draft))
    row = add_row(data, "sections", kind="after_payment", body="قدم اول")
    data[f"{row}-image"] = SimpleUploadedFile("step.png", png((800, 500)), content_type="image/png")

    response = client.post(change_url(draft), data)

    assert response.status_code == 200
    assert "متن جایگزین" in errors_under(response.content.decode(), f"{row}-image_alt")
    assert not draft.sections.exists()

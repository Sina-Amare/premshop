"""Conversational storefront copy stays precise; email keeps written Persian."""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.views.defaults import permission_denied
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product
from apps.catalog.promises import PROMISES
from apps.core import ratelimit
from apps.core.email import render_email
from apps.core.errors import server_error
from apps.core.views import EMAIL_PREVIEWS

pytestmark = pytest.mark.django_db

# (form, why). Words, not substrings: «رو» is inside «روی» and «روز».
BANNED = [
    ("می‌تونین", "«می‌تونید»"),
    ("می‌خواین", "«می‌خواید»"),
    ("ببینین", "«ببینید»"),
    ("—", "an em dash in Persian text: «،» or a full stop, and «|» in a title"),
    ("بهترین", "a number, not a superlative"),
    ("سریع‌ترین", "a number, not a superlative"),
    ("تخفیف‌دارها", "«تخفیف‌ها»"),
    ("پیدا نشد", "«پیدا نکردیم»"),
    ("مشاهده همه", "«مشاهده‌ی همه»"),
    ("کارت‌های هدیه", "«گیفت کارت»"),
    ("گیفت‌کارت", "«گیفت کارت», two words"),
    ("شماره تماس", "«شماره‌ی موبایل»"),
    ("عضو از", "«تاریخ عضویت»"),
    ("کد تأیید", "«کد ورود»"),
    ("برمی‌گرده", "«برمی‌گردانیم», with its condition"),
    ("ضمانت بازگشت وجه", "«بازگشت وجه», with its condition"),
    ("تومن", "«تومان»"),
]
WRITTEN_ONLY = [
    ("رو", "«را»"),
    ("می‌شه", "«می‌شود»"),
    ("نمی‌دیم", "«نمی‌دهیم»"),
    ("مونده", "«مانده»"),
    ("باقی‌مونده", "«باقی‌مانده»"),
    ("تموم", "«تمام»"),
    ("دیگه", "«دیگر»"),
    ("خودتون", "«خودتان»"),
    ("همون", "«همان»"),
    ("اینا", "«این‌ها» or a noun"),
    ("هرچی", "«هر چه» or a noun"),
]
PATTERNS = [
    (form, re.compile(rf"(?<![\w\u200c]){re.escape(form)}(?![\w\u200c])"), why)
    for form, why in BANNED
]


class _Said(HTMLParser):
    """The text the shop itself says: no script or style, and no FAQ question,
    which is the buyer talking."""

    SKIP = {"script", "style", "summary", "dt"}

    def __init__(self) -> None:
        super().__init__()
        self.depth, self.parts, self.title, self.in_title = 0, [], "", False
        self.metadata = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.depth += 1
        self.in_title = tag == "title"
        attributes = dict(attrs)
        if tag == "meta" and (attributes.get("name") or attributes.get("property")) in {
            "description",
            "og:title",
            "og:description",
            "twitter:title",
            "twitter:description",
        }:
            self.metadata.append(attributes.get("content") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        elif not self.depth:
            self.parts.append(data)


def said(body: str) -> tuple[str, str]:
    parser = _Said()
    parser.feed(body)
    return " ".join(" ".join(parser.parts).split()), parser.title


def offences(text: str, *, written: bool = False) -> list[str]:
    found = [f"{form}: {why}" for form, pattern, why in PATTERNS if pattern.search(text)]
    if written:
        found += [
            form
            for form, _why in WRITTEN_ONLY
            if re.search(rf"(?<![\w‌]){re.escape(form)}(?![\w‌])", text)
        ]
    return found


class _Commitments(HTMLParser):
    CLASSES = {
        "faq__answer",
        "qa__a",
        "delivery-answer",
        "price",
        "deadline",
        "deadline__said",
        "cart-line__warning",
        "cart-line__error",
        "cart-summary__blocked",
        "cart-notice",
        "cart-add-error",
        "v-hero__lead",
    }
    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__()
        self.stack, self.parts = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            classes = set(dict(attrs).get("class", "").split())
            self.stack.append(bool(self.CLASSES & classes) or any(self.stack))

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if any(self.stack):
            self.parts.append(data)


@pytest.fixture
def shop():
    subs = Category.objects.create(name="هوش مصنوعی", slug="ai")
    gifts = Category.objects.create(name="گیفت کارت", slug="gift")
    chat = Product.objects.create(
        category=subs,
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        warranty=Product.Warranty.FULL_PERIOD,
        delivery_hours=12,
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(
        product=chat,
        title="۱ ماهه",
        cost_price=Decimal(1),
        sale_price=Decimal(1390000),
        promo_price=Decimal(1190000),
        promo_ends_at=timezone.now() + timedelta(days=3),
        requires_customer_input=True,
        customer_input_label="ایمیل اکانت OpenAI",
    )
    card = Product.objects.create(
        category=gifts,
        name="Apple Gift Card",
        slug="apple-gift-card",
        delivery_type=Product.DeliveryType.GIFT_CARD,
        warranty=Product.Warranty.NONE,
        delivery_hours=1,
        delivery_note="۴۸ تا ۷۲ ساعت",
        status=Product.Status.ACTIVE,
    )
    Plan.objects.create(product=card, title="۱۰ دلاری", cost_price=1, sale_price=1180000)
    Plan.objects.create(
        product=card, title="۲۵ دلاری", cost_price=1, sale_price=2900000, is_available=False
    )


def rendered_pages(client) -> dict[str, str]:
    pages = {
        name: client.get(url).content.decode()
        for name, url in {
            "home": "/",
            "category": "/c/ai/",
            "subscription": "/p/chatgpt-plus/",
            "gift card": "/p/apple-gift-card/",
            "faq": "/faq/",
            "search hit": "/search/?q=chatgpt",
            "search miss": "/search/?q=xbox",
            "404": "/no-such-page/",
            "sign-in": "/login/",
            "code request": "/login/code/",
            "cart empty": "/cart/",
        }.items()
    }
    cart_plan = Plan.objects.filter(product__slug="chatgpt-plus", is_available=True).first()
    client.post("/cart/add/", {"plan": cart_plan.pk})
    pages["cart populated"] = client.get("/cart/").content.decode()
    pages["cart error"] = client.post(
        "/cart/quantity/", {"plan": cart_plan.pk, "quantity": 99}
    ).content.decode()
    customer = Client()
    customer.force_login(User.objects.create_user("customer@example.test", is_verified=True))
    pages["account"] = customer.get(reverse("account")).content.decode()
    pages["account password"] = customer.get(reverse("account-password")).content.decode()
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    pages["403"] = permission_denied(request, PermissionDenied()).content.decode()
    pages["403 csrf"] = Client(enforce_csrf_checks=True).post(reverse("login"), {}).content.decode()
    pages["500"] = server_error(request).content.decode()
    with mock.patch("apps.core.ratelimit.cache.add", side_effect=RedisConnectionError("refused")):
        pages["503"] = client.post(
            reverse("login-code"), {"email": "x@example.test"}
        ).content.decode()
    for _ in range(ratelimit.SEARCH_PER_IP.times):
        client.get("/search/?q=a")
    pages["search, rate-limited"] = client.get("/search/?q=a").content.decode()
    return pages


def test_every_storefront_page_speaks_written_persian(client, shop):
    pages = rendered_pages(client)
    assert len(pages) == 20

    bad = {}
    for name, body in pages.items():
        text, title = said(body)
        assert text, f"nothing read on {name}"
        found = offences(text) + [f"title: {o}" for o in offences(title, written=True)]
        if found:
            bad[name] = found
    assert not bad


def test_every_email_speaks_written_persian_in_both_halves():
    bad = {}
    for name, context in EMAIL_PREVIEWS.items():
        subject, text, html = render_email(name, context)
        # The plain-text half draws its rules as a line of dashes, not a sentence.
        text = re.sub(r"(?m)^\s*—+\s*$", "", text)
        body, title = said(html)
        assert body and text, f"nothing read in {name}"
        found = (
            offences(subject, written=True)
            + offences(text, written=True)
            + offences(body, written=True)
            + offences(title, written=True)
        )
        if found:
            bad[name] = found
    assert len(EMAIL_PREVIEWS) == 3
    assert not bad


def test_activation_answer_does_not_make_a_blanket_password_promise(client):
    body = client.get(reverse("faq")).content.decode()
    answer = re.search(
        r"برای فعال‌سازی باید رمز اکانتم رو بدم؟</summary>\s*<div[^>]*><p[^>]*>(.*?)</p>",
        body,
        re.S,
    )
    assert answer, "the activation question was not checked"
    text = said(answer[1])[0]
    assert "نه، فقط ایمیل" not in text
    assert "سایت" in text and "وارد نکنید" in text
    assert not offences(text, written=True)


def test_product_activation_answer_does_not_promise_email_is_sufficient(client, shop):
    body = client.get("/p/chatgpt-plus/").content.decode()
    answer = re.search(
        r"برای فعال‌سازی باید رمز اکانتم رو بدم؟</dt><dd[^>]*>(.*?)</dd>", body, re.S
    )
    assert answer, "the product activation answer was not checked"
    text = said(answer[1])[0]
    assert "فقط" not in text
    assert "سایت" in text and "وارد نکنید" in text


def test_faq_describes_access_limits_without_vpn_guidance(client):
    text = said(client.get(reverse("faq")).content.decode())[0]
    assert "محدودیت" in text
    assert "فیلترشکن" not in text


def test_conversational_guidance_and_written_commitments_have_distinct_rules():
    assert not offences("سرویس موردنظرتون رو می‌تونید انتخاب کنید.")
    assert offences("سرویس موردنظرتون رو می‌تونید انتخاب کنید.", written=True)
    assert PROMISES
    assert not [
        (promise.text, offences(promise.text, written=True))
        for promise in PROMISES
        if offences(promise.text, written=True)
    ]


def test_money_and_deadline_regions_keep_written_persian(client, shop):
    captured, bad = 0, {}
    for name, body in rendered_pages(client).items():
        parser = _Commitments()
        parser.feed(body)
        captured += len(parser.parts)
        found = offences(" ".join(parser.parts), written=True)
        if found:
            bad[name] = found
    assert captured > 10, "no purchase facts were checked"
    assert not bad


def test_product_delivery_answer_is_written(client, shop):
    body = client.get("/p/chatgpt-plus/").content.decode()
    answer = re.search(r'<dt id="delivery">.*?</dt><dd[^>]*>(.*?)</dd>', body, re.S)
    assert answer, "the delivery commitment was not checked"
    assert not offences(said(answer[1])[0], written=True)


def test_seo_titles_and_metadata_keep_written_persian(client, shop):
    checked, bad = 0, {}
    for name, body in rendered_pages(client).items():
        parser = _Said()
        parser.feed(body)
        checked += len(parser.metadata)
        found = offences(" ".join([parser.title, *parser.metadata]), written=True)
        if found:
            bad[name] = found
    assert checked > 20, "no SEO attributes were checked"
    assert not bad


def test_seo_scan_reads_attributes_and_titles_without_scanning_guidance():
    parser = _Said()
    parser.feed(
        '<title>می‌شه</title><meta name="description" content="این رو ببینید"><p>همون رو ببینید.</p>'
    )
    assert offences(parser.title, written=True) == ["می‌شه"]
    assert offences(" ".join(parser.metadata), written=True) == ["رو"]


def test_commitment_scan_catches_nested_text_without_banning_casual_guidance():
    parser = _Commitments()
    parser.feed('<p>این رو ببینید.</p><span class="price">این <bdi>می‌شه</bdi> خرید.</span>')
    assert "رو" not in parser.parts
    assert offences(" ".join(parser.parts), written=True) == ["می‌شه"]


# A quoted string with a Persian letter in it, inside a page's script.
SCRIPT_STRING = re.compile(r"""(['"`])((?:(?!\1).)*?[؀-ۿ](?:(?!\1).)*?)\1""")


def script_strings() -> list[tuple[str, str]]:
    """What the pages' scripts write into the page after it has loaded, which
    the rendered-page scan above never sees."""
    root = Path(settings.BASE_DIR)
    found = []
    templates = root / "templates"
    for path in [
        *templates.rglob("*.html"),
        *templates.rglob("*.js"),
        *(root / "static" / "js").rglob("*.js"),
    ]:
        text = path.read_text(encoding="utf-8")
        blocks = (
            [text] if path.suffix == ".js" else re.findall(r"<script\b.*?</script>", text, re.S)
        )
        found += [(path.name, s) for block in blocks for _, s in SCRIPT_STRING.findall(block)]
    return found


def test_the_scripts_write_written_persian_too():
    strings = script_strings()
    assert ("base.html", "تخفیف تمام شد") in strings, "positive control: the walk read a script"

    assert not [(where, s, offences(s)) for where, s in strings if offences(s)]


@pytest.mark.parametrize("form, why", BANNED)
def test_the_scan_catches_each_banned_form(form, why):
    body = f"<title>x</title><p>اول {form} آخر</p><summary>اکانتش رو می‌خوام</summary>"

    assert offences(said(body)[0]), form


def test_the_scan_leaves_the_buyers_questions_and_good_persian_alone():
    body = (
        "<title>پرسش‌های متداول | PremShop</title>"
        "<summary>رمز اکانت خودم رو باید بدم؟</summary>"
        "<p>روی اکانت خودتان فعال می‌کنیم. ۱۳ روز مانده. روزهای باقی‌مانده.</p>"
    )
    text, title = said(body)

    assert not offences(text) and not offences(title)

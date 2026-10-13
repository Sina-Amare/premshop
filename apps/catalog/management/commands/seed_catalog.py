"""Fill the shop with realistic mock products so the pages can be judged full.

Development only: the owner has no product list yet (open-questions #2), and a
catalog page with one product tells you nothing about how the grid, the
prices, the search or the empty states behave. Idempotent — run it twice and
you get the same shop. `--reset` wipes what it made first.

Brand marks are downloaded once into MEDIA_ROOT, which is not committed, and
served from there — the running site never reaches a CDN, because most of them
are unreachable from Iran. If the network is unavailable the product simply has
no image, which the cards are designed for.
"""

from __future__ import annotations

import re
import urllib.request
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product, ProductSpec

# One source per brand, because no single icon set has all twelve in colour.
# gilbarbara/logos carries the full-colour marks; lobehub has the AI brands the
# icon CDNs never added. The CDN we used before (cdn.simpleicons.org) answers
# 404 for openai, adobe and midjourney — which is exactly why three products
# fell back to a letter — and serves one flat colour per mark even when it
# answers, so it structurally cannot give us the four-colour Google Play
# triangle. Every URL below was fetched and the file inspected on 2026-10-13.
GB = "https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/"
LH = "https://unpkg.com/@lobehub/icons-static-svg@1.95.1/icons/"

# slug → (url, pad). Five of these marks are bare glyphs that float in
# whitespace (the Apple apple, the Adobe A, the OpenAI knot) and three bring
# their own coloured container (the Spotify circle, the YouTube rectangle, the
# JetBrains square). Rendered at one size the glyphs tower over the containers
# and the containers read as stickers lost in a box, so the padding that evens
# them out is baked into the file at download time — see `_normalise_mark`.
MARKS = {
    "claude-pro": (GB + "claude-icon.svg", 0.16),
    "chatgpt-plus": (GB + "openai-icon.svg", 0.16),
    "gemini-advanced": (LH + "gemini-color.svg", 0.10),
    "github-copilot": (GB + "github-copilot.svg", 0.16),
    "jetbrains-all": (GB + "jetbrains.svg", 0),
    "spotify-premium": (GB + "spotify-icon.svg", 0),
    "youtube-premium": (GB + "youtube-icon.svg", 0.06),
    "netflix": ("https://cdn.simpleicons.org/netflix", 0.16),
    "apple-gift-card": (GB + "apple.svg", 0.27),
    "google-play-gift-card": (GB + "google-play-icon.svg", 0.16),
    "midjourney": (GB + "midjourney.svg", 0.16),
    "adobe-cc": (GB + "adobe-icon.svg", 0.16),
}

T = Product.DeliveryType
W = Product.Warranty


def _normalise_mark(svg: bytes, pad: float) -> bytes:
    """Give every brand mark the same optical size inside an identical tile.

    Widening the viewBox around its own centre shrinks the artwork inside an
    unchanged box — the one adjustment that evens a shelf of twelve logos
    without touching a single path. Scaling the `<img>` instead would need a
    per-brand CSS rule, and the knowledge of what each file looks like belongs
    where the file is acquired, not in the stylesheet.
    """
    if not pad:
        return svg
    match = re.search(rb'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)\s*"', svg)
    if not match:
        return svg
    x, y, w, h = (float(value) for value in match.groups())
    box = (
        f'viewBox="{x - w * pad:g} {y - h * pad:g} ' f'{w * (1 + 2 * pad):g} {h * (1 + 2 * pad):g}"'
    )
    return svg[: match.start()] + box.encode() + svg[match.end() :]


CATEGORIES = [
    ("هوش مصنوعی", "ai", "اشتراک ابزارهای هوش مصنوعی، تحویل سریع و با گارانتی."),
    ("ابزار توسعه", "dev", "لایسنس و اشتراک ابزارهای برنامه‌نویسی."),
    ("سرگرمی", "media", "موسیقی و فیلم، روی اکانت خودتان یا اکانت آماده."),
    ("گیفت کارت", "gift", "کارت‌های هدیه با کد، تحویل فوری."),
]

# name, slug, category, delivery, warranty, hours, short, aliases, specs, plans
PRODUCTS: list[dict[str, Any]] = [
    dict(
        name="Claude Pro",
        slug="claude-pro",
        renewable=True,
        aliases="کلاود پرو، کلود پرو، کلاد، claude، anthropic، آنتروپیک",
        category="ai",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        warranty=W.FULL_PERIOD,
        hours=12,
        short="اشتراک Claude Pro روی اکانت خودتان؛ مدل‌های جدید و سقف پیام بالاتر.",
        specs=[
            ("نوع اشتراک", "Pro"),
            ("فعال‌سازی", "روی ایمیل شما"),
            ("پشتیبانی", "تا پایان دوره"),
        ],
        plans=[("۱ ماهه", 30, 900000, 1450000, None), ("۳ ماهه", 90, 2600000, 3900000, 3500000)],
        input="ایمیل حساب Claude",
    ),
    dict(
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        renewable=True,
        aliases="چت جی پی تی، چت جیپیتی، چت جیپت، چت گپت، جیپیتی، gpt، chatgpt، openai، اوپن ای آی",
        category="ai",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        warranty=W.FULL_PERIOD,
        hours=12,
        short="اشتراک ChatGPT Plus روی اکانت خودتان.",
        specs=[("نوع اکانت", "اختصاصی", True), ("نوع اشتراک", "Plus")],
        plans=[("۱ ماهه", 30, 850000, 1390000, None)],
        input="ایمیل حساب OpenAI",
    ),
    dict(
        name="Gemini Advanced",
        slug="gemini-advanced",
        renewable=True,
        aliases="جمینای، جمنای، جیمینای، gemini، هوش مصنوعی گوگل",
        category="ai",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        warranty=W.DAYS_7,
        hours=24,
        short="Gemini Advanced به همراه ۲ ترابایت فضای Google One.",
        specs=[("نوع اکانت", "اختصاصی", True), ("فضای ابری", "۲ ترابایت")],
        plans=[("۱ ماهه", 30, 700000, 1190000, None), ("۲ ماهه", 60, 1300000, 2290000, None)],
        input="آدرس Gmail",
    ),
    dict(
        name="GitHub Copilot",
        slug="github-copilot",
        renewable=True,
        aliases="گیت هاب کوپایلوت، کوپایلوت، کوپیلوت، copilot، github",
        category="dev",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        warranty=W.FULL_PERIOD,
        hours=24,
        short="Copilot روی اکانت GitHub خودتان؛ برای VS Code و JetBrains.",
        specs=[("پلن", "Individual"), ("محیط‌ها", "VS Code · JetBrains · Neovim")],
        plans=[("۱ ماهه", 30, 550000, 890000, None), ("۱۲ ماهه", 365, 5200000, 8900000, 7900000)],
        input="نام کاربری GitHub",
    ),
    dict(
        name="JetBrains All Products",
        slug="jetbrains-all",
        max_quantity=3,
        aliases="جت برینز، جتبرینز، intellij، pycharm، webstorm، jetbrains، پای چارم",
        category="dev",
        delivery=T.CODE_LICENSE,
        warranty=W.FULL_PERIOD,
        hours=48,
        short="لایسنس همه‌ی محصولات JetBrains، فعال‌سازی با کد.",
        specs=[("شامل", "IntelliJ · PyCharm · WebStorm · و بقیه"), ("فعال‌سازی", "کد لایسنس")],
        plans=[("۱۲ ماهه", 365, 3900000, 6400000, None)],
    ),
    dict(
        name="Spotify Premium",
        slug="spotify-premium",
        renewable=True,
        aliases="اسپاتیفای، اسپوتیفای، اسپاتی فای، اسپتیفای، spotify، پریمیوم، پرمیوم",
        category="media",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        warranty=W.FULL_PERIOD,
        hours=6,
        short="Spotify Premium روی اکانت خودتان.",
        specs=[("نوع اکانت", "اختصاصی", True), ("ریجن", "ترکیه")],
        plans=[
            ("۱ ماهه", 30, 90000, 190000, None),
            ("۳ ماهه", 90, 250000, 490000, 420000),
            ("۱۲ ماهه", 365, 900000, 1690000, None),
        ],
        input="ایمیل حساب Spotify",
    ),
    dict(
        name="YouTube Premium",
        slug="youtube-premium",
        renewable=True,
        aliases="یوتیوب پریمیوم، یوتیوب پرمیوم، یوتوب، یوتیوب بدون تبلیغات، youtube، پریمیوم، پرمیوم",
        category="media",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        warranty=W.DAYS_7,
        hours=12,
        short="YouTube Premium بدون تبلیغ، به همراه YouTube Music.",
        specs=[("نوع اکانت", "اختصاصی", True), ("ریجن", "ترکیه"), ("شامل", "YouTube Music")],
        plans=[("۱ ماهه", 30, 120000, 240000, None)],
        input="آدرس Gmail",
    ),
    dict(
        name="Netflix",
        slug="netflix",
        renewable=True,
        aliases="نتفلیکس، نت فلیکس، نتفلیس، نتلفیکس، netflix",
        category="media",
        delivery=T.READY_ACCOUNT,
        warranty=W.FULL_PERIOD,
        hours=2,
        short="اکانت آماده‌ی Netflix، یک پروفایل اختصاصی.",
        specs=[
            ("نوع اکانت", "اختصاصی", True),
            ("پخش هم‌زمان", "۲ دستگاه", True),
            ("پلن", "Standard"),
            ("کیفیت", "Full HD"),
        ],
        plans=[("۱ ماهه", 30, 200000, 390000, 340000)],
    ),
    dict(
        name="Apple Gift Card",
        slug="apple-gift-card",
        max_quantity=5,
        aliases="گیفت کارت اپل، گیفتکارت اپل، کارت هدیه اپل، آیتونز، itunes، apple",
        category="gift",
        delivery=T.GIFT_CARD,
        warranty=W.NONE,
        hours=1,
        short="گیفت کارت Apple آمریکا، تحویل کد.",
        specs=[("ریجن", "آمریکا", True), ("تحویل", "کد ۱۶ رقمی")],
        plans=[
            ("۱۰ دلاری", None, 1050000, 1180000, None),
            ("۲۵ دلاری", None, 2600000, 2900000, None),
        ],
    ),
    dict(
        name="Google Play Gift Card",
        slug="google-play-gift-card",
        max_quantity=5,
        aliases="گیفت کارت گوگل پلی، گیفتکارت گوگل، گوگل پلی، google play، کارت هدیه گوگل",
        category="gift",
        delivery=T.GIFT_CARD,
        warranty=W.NONE,
        hours=1,
        short="گیفت کارت Google Play آمریکا.",
        specs=[("ریجن", "آمریکا")],
        plans=[("۱۰ دلاری", None, 1050000, 1180000, None)],
    ),
    # One draft and one unavailable, so "hidden from visitors" can be seen to hold.
    dict(
        name="Midjourney",
        slug="midjourney",
        aliases="میدجرنی، میدجورنی، مید جرنی، midjourney",
        category="ai",
        delivery=T.READY_ACCOUNT,
        warranty=W.DAYS_7,
        hours=24,
        short="پیش‌نویس — هنوز منتشر نشده.",
        specs=[],
        plans=[("۱ ماهه", 30, 500000, 900000, None)],
        status=Product.Status.DRAFT,
    ),
    dict(
        name="Adobe Creative Cloud",
        slug="adobe-cc",
        aliases="ادوبی، ادوب، فتوشاپ، photoshop، adobe، creative cloud، کریتیو کلاد",
        category="dev",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        warranty=W.DAYS_7,
        hours=48,
        short="فعلاً ناموجود.",
        specs=[],
        plans=[("۱ ماهه", 30, 1500000, 2400000, None)],
        status=Product.Status.UNAVAILABLE,
    ),
]


class Command(BaseCommand):
    help = "Seed the catalog with mock categories, products, specs and plans (development only)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--reset", action="store_true", help="delete the seeded rows first")
        parser.add_argument("--no-images", action="store_true", help="skip fetching icons")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            self.stderr.write("Refusing to seed a non-DEBUG database.")
            return
        if options["reset"]:
            Product.objects.filter(slug__in=[p["slug"] for p in PRODUCTS]).delete()
            Category.objects.filter(slug__in=[c[1] for c in CATEGORIES], products=None).delete()

        categories = {}
        for order, (name, slug, description) in enumerate(CATEGORIES):
            categories[slug], _ = Category.objects.update_or_create(
                slug=slug, defaults={"name": name, "description": description, "sort_order": order}
            )

        now = timezone.now()
        for spec in PRODUCTS:
            product, _ = Product.objects.update_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "category": categories[spec["category"]],
                    "short_description": spec["short"],
                    "full_description": spec.get("long", spec["short"]),
                    "delivery_type": spec["delivery"],
                    "aliases": spec.get("aliases", ""),
                    "warranty": spec["warranty"],
                    "delivery_hours": spec["hours"],
                    "status": spec.get("status", Product.Status.ACTIVE),
                    "renewable": spec.get("renewable", False),
                    "seo_title": f"خرید {spec['name']}",
                    "seo_description": spec["short"][:160],
                },
            )
            product.specs.all().delete()
            for order, row in enumerate(spec["specs"]):
                title, value, *key = row
                ProductSpec.objects.create(
                    product=product,
                    title=title,
                    value=value,
                    is_key=bool(key and key[0]),
                    sort_order=order,
                )
            product.plans.all().delete()
            for order, (title, days, cost, sale, promo) in enumerate(spec["plans"]):
                Plan.objects.create(
                    product=product,
                    title=title,
                    duration_days=days,
                    cost_price=Decimal(cost),
                    sale_price=Decimal(sale),
                    promo_price=Decimal(promo) if promo else None,
                    promo_starts_at=now - timedelta(days=1) if promo else None,
                    promo_ends_at=now + timedelta(days=14) if promo else None,
                    requires_customer_input=bool(spec.get("input")),
                    customer_input_label=spec.get("input", ""),
                    # A plan that asks for the buyer's own account details is
                    # one per order by database CHECK — there is one box to
                    # type the email into.
                    max_quantity=1 if spec.get("input") else spec.get("max_quantity", 1),
                    sort_order=order,
                )
            if not options["no_images"]:
                self._attach_icon(product, *MARKS[spec["slug"]])
            self.stdout.write(f"  {product.name}: {product.plans.count()} plans")
        self.stdout.write(
            self.style.SUCCESS(f"seeded {len(PRODUCTS)} products in {len(CATEGORIES)} categories")
        )

    def _attach_icon(self, product: Product, url: str, pad: float) -> None:
        target = Path(settings.MEDIA_ROOT) / "products" / f"{product.slug}.svg"
        target.parent.mkdir(parents=True, exist_ok=True)
        body: bytes | None = None
        try:
            # Some logo hosts answer 403 to a library's default user agent.
            request = urllib.request.Request(  # noqa: S310  # nosec B310
                url, headers={"User-Agent": "premshop-seed"}
            )
            opened = urllib.request.urlopen(request, timeout=15)  # noqa: S310  # nosec B310
            with opened as response:
                body = response.read()
        except Exception as exc:  # noqa: BLE001 — no image is a designed state, not a failure
            self.stderr.write(f"  no mark for {product.name}: {type(exc).__name__}")
        # A 404 from a logo host is an HTML page, and `startswith(b"<svg")` is
        # the wrong check: most of these files open with an XML declaration.
        # The previous version wrote whatever came back and then skipped the
        # fetch forever because the file existed, so one bad answer was
        # permanent. Nothing is written unless it parses as a mark.
        if body is not None and (b"<svg" not in body[:400] or b"viewBox" not in body):
            self.stderr.write(f"  not an svg for {product.name}: {len(body)} bytes")
            body = None
        if body is not None:
            target.write_bytes(_normalise_mark(body, pad))
        if not target.exists():
            return
        product.image.name = f"products/{product.slug}.svg"
        product.save(update_fields=["image"])

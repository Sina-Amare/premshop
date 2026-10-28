"""Fill the shop with realistic mock products so the pages can be judged full.

Development only: the owner has no product list yet (open-questions #2), and a
catalog page with one product tells you nothing about how the grid, the
prices, the search or the empty states behave. It only adds what is missing: a
category or product whose slug already exists is left exactly as it is, with
its plans, specs and image, because the operator may have edited it and an
order line holds its plan by PROTECT. `--reset` wipes what it made first.

Base catalogue marks are copied from retained files into MEDIA_ROOT and
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
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection
from django.utils import timezone

from apps.catalog.demo_content import PRODUCT_COPY
from apps.catalog.models import Category, Plan, Product, ProductSection, ProductSpec

# The base catalogue uses retained assets; extended demo services have a URL fallback.
GB = "https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/"
LH = "https://unpkg.com/@lobehub/icons-static-svg@1.95.1/icons/"
MARK_DIR = Path(__file__).resolve().parents[2] / "marks"
MARKS = {
    slug: (MARK_DIR / f"{slug}.svg", pad)
    for slug, pad in {
        "claude-pro": 0.16,
        "chatgpt-plus": 0.16,
        "gemini-advanced": 0.10,
        "github-copilot": 0.16,
        "jetbrains-all": 0.18,
        "spotify-premium": 0.10,
        "youtube-premium": 0.06,
        "netflix": 0.16,
        "apple-gift-card": 0.27,
        "google-play-gift-card": 0.16,
        "midjourney": 0.16,
        "adobe-cc": 0.16,
    }.items()
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
    match = re.search(rb'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)\s*"', svg)
    if not match:
        return svg
    x, y, w, h = (float(value) for value in match.groups())
    size = max(w, h) * (1 + 2 * pad)
    box = f'viewBox="{x + w / 2 - size / 2:g} {y + h / 2 - size / 2:g} {size:g} {size:g}"'
    svg = svg[: match.start()] + box.encode() + svg[match.end() :]
    root = re.search(rb"<svg\b[^>]*>", svg)
    if root is None:
        return svg
    tag = re.sub(rb'\s(?:width|height)="[^"]*"', b"", root.group())
    tag = tag.replace(b"<svg", f'<svg width="{size:g}" height="{size:g}"'.encode(), 1)
    return svg[: root.start()] + tag + svg[root.end() :]


CATEGORIES = [
    ("هوش مصنوعی", "ai", "اشتراک ChatGPT،\u200f Claude و Gemini، روی اکانت خودتان."),
    ("ابزار توسعه", "dev", "لایسنس و اشتراک ابزارهای برنامه‌نویسی."),
    ("سرگرمی", "media", "موسیقی و فیلم، روی اکانت خودتان یا اکانت آماده."),
    ("گیفت کارت", "gift", "گیفت کارت Apple و Google Play آمریکا، تحویل به‌صورت کد."),
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
        specs=[("نوع اشتراک", "Pro")],
        plans=[("۱ ماهه", 30, 900000, 1450000, None), ("۳ ماهه", 90, 2600000, 3900000, 3500000)],
        input="ایمیل اکانت Claude",
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
        specs=[("نوع اشتراک", "Plus")],
        plans=[("۱ ماهه", 30, 850000, 1390000, None)],
        input="ایمیل اکانت OpenAI",
    ),
    dict(
        name="Gemini Advanced",
        slug="gemini-advanced",
        brand_colour="#3186ff",
        renewable=True,
        aliases="جمینای، جمنای، جیمینای، gemini، هوش مصنوعی گوگل",
        category="ai",
        delivery="",
        warranty=W.DAYS_7,
        hours=24,
        short="Gemini Advanced با امکانات Google AI Pro، بسته به پلن انتخابی.",
        specs=[("فضای ابری", "بسته به پلن انتخابی")],
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
        specs=[("ریجن", "ترکیه")],
        plans=[
            ("۱ ماهه", 30, 90000, 190000, None),
            ("۳ ماهه", 90, 250000, 490000, 420000),
            ("۱۲ ماهه", 365, 900000, 1690000, None),
        ],
        input="ایمیل اکانت Spotify",
    ),
    dict(
        name="YouTube Premium",
        slug="youtube-premium",
        brand_colour="#ff0033",
        renewable=True,
        aliases="یوتیوب پریمیوم، یوتیوب پرمیوم، یوتوب، یوتیوب بدون تبلیغات، youtube، پریمیوم، پرمیوم",
        category="media",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        warranty=W.DAYS_7,
        hours=12,
        short="YouTube Premium بدون تبلیغ، به همراه YouTube Music.",
        specs=[("ریجن", "ترکیه"), ("شامل", "YouTube Music")],
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
        short="اکانت آماده‌ی Netflix: یک پروفایل اختصاصی روی اکانت اشتراکی.",
        specs=[
            ("نوع اکانت", "اشتراکی (پروفایل اختصاصی)", True),
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
        short="پیش‌نویس، هنوز منتشر نشده.",
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


EXTENDED_MARKS = {
    "perplexity-pro": (LH + "perplexity-color.svg", 0.10),
    "poe": (LH + "poe-color.svg", 0.10),
    "runway": (LH + "runway.svg", 0.12),
    "cursor-pro": (LH + "cursor.svg", 0.12),
    "github-pro": (LH + "github.svg", 0.16),
    "replit-core": (LH + "replit-color.svg", 0.10),
    "docker-pro": (GB + "docker-icon.svg", 0.08),
    "apple-music": ("https://cdn.simpleicons.org/applemusic", 0.12),
    "disney-plus": (
        "https://raw.githubusercontent.com/pheralb/svgl/main/static/library/disneyplus.svg",
        0.08,
    ),
    "crunchyroll": ("https://cdn.simpleicons.org/crunchyroll", 0.12),
    "steam-gift-card": (GB + "steam.svg", 0.12),
    "playstation-gift-card": ("https://cdn.simpleicons.org/playstation", 0.12),
    "xbox-gift-card": (
        "https://raw.githubusercontent.com/pheralb/svgl/main/static/library/xbox.svg",
        0.12,
    ),
    "amazon-gift-card": (
        "https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/amazon.svg",
        0.12,
    ),
}
EXTENDED_PRODUCTS = []
for index, (name, slug, category, colour) in enumerate(
    [
        ("Perplexity Pro", "perplexity-pro", "ai", "#20808d"),
        ("Poe", "poe", "ai", "#5d5cde"),
        ("Runway", "runway", "ai", "#343434"),
        ("Cursor Pro", "cursor-pro", "dev", "#343434"),
        ("GitHub Pro", "github-pro", "dev", "#24292f"),
        ("Replit Core", "replit-core", "dev", "#f26207"),
        ("Docker Pro", "docker-pro", "dev", "#2496ed"),
        ("Apple Music", "apple-music", "media", "#fa243c"),
        ("Disney+", "disney-plus", "media", "#113ccf"),
        ("Crunchyroll", "crunchyroll", "media", "#f47521"),
        ("Steam Gift Card", "steam-gift-card", "gift", "#175788"),
        ("PlayStation Gift Card", "playstation-gift-card", "gift", "#0070cc"),
        ("Xbox Gift Card", "xbox-gift-card", "gift", "#107c10"),
        ("Amazon Gift Card", "amazon-gift-card", "gift", "#ff9900"),
    ]
):
    base = 350000 + index * 75000
    gift = category == "gift"
    EXTENDED_PRODUCTS.append(
        dict(
            name=name,
            slug=slug,
            category=category,
            brand_colour=colour,
            delivery=T.GIFT_CARD if gift else T.READY_ACCOUNT,
            warranty=W.NONE,
            hours=24,
            short="محصول نمونه برای بررسی ظاهر فروشگاه؛ قیمت و شرایط فروش واقعی نیست.",
            usage_notes="اطلاعات این محصول آزمایشی‌ست؛ قبل از فروش، شرایط سرویس باید بررسی بشه.",
            specs=[("اطلاعات محصول", "نمونه‌ی آزمایشی")],
            plans=[
                (
                    "۱۰ دلاری" if gift else "۱ ماهه",
                    None if gift else 30,
                    base,
                    base + 150000,
                    base + 100000 if index % 3 == 0 else None,
                ),
                (
                    "۲۵ دلاری" if gift else "۳ ماهه",
                    None if gift else 90,
                    base * 2,
                    base * 2 + 250000,
                    None,
                ),
            ],
        )
    )


class Command(BaseCommand):
    help = "Seed the catalog with mock categories, products, specs and plans (development only)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--extended",
            action="store_true",
            help="add a balanced review catalog to the isolated local review database",
        )
        parser.add_argument("--reset", action="store_true", help="delete the seeded rows first")
        parser.add_argument("--no-images", action="store_true", help="skip fetching icons")

    def handle(self, *args: Any, **options: Any) -> None:
        extended = options["extended"]
        if extended and (
            not settings.DEBUG
            or not getattr(settings, "DEMO_CATALOG", False)
            or connection.settings_dict["NAME"]
            not in {"premshop_ui_review", "test_premshop_ui_review"}
            or connection.settings_dict.get("HOST") not in {"localhost", "127.0.0.1", "::1"}
            or options["reset"]
        ):
            raise CommandError(
                "Extended seeding requires DEBUG, DEMO_CATALOG and the local premshop_ui_review database, without --reset."
            )
        products = PRODUCTS + EXTENDED_PRODUCTS if extended else PRODUCTS
        marks = MARKS | EXTENDED_MARKS if extended else MARKS
        if not settings.DEBUG:
            self.stderr.write("Refusing to seed a non-DEBUG database.")
            return
        if options["reset"]:
            Product.objects.filter(slug__in=[p["slug"] for p in PRODUCTS]).delete()
            Category.objects.filter(slug__in=[c[1] for c in CATEGORIES], products=None).delete()

        categories = {}
        for order, (name, slug, description) in enumerate(CATEGORIES):
            categories[slug], _ = Category.objects.get_or_create(
                slug=slug, defaults={"name": name, "description": description, "sort_order": order}
            )

        now = timezone.now()
        added = 0
        for spec in products:
            copy = PRODUCT_COPY.get(spec["slug"], {})
            product, created = Product.objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "brand_colour": spec.get("brand_colour", ""),
                    "usage_notes": spec.get("usage_notes", ""),
                    "category": categories[spec["category"]],
                    "short_description": copy.get("short", spec["short"]),
                    "delivery_type": spec["delivery"],
                    "aliases": spec.get("aliases", ""),
                    "warranty": spec["warranty"],
                    "delivery_hours": spec["hours"],
                    "status": spec.get("status", Product.Status.ACTIVE),
                    "renewable": spec.get("renewable", False),
                    "seo_title": copy.get("title", f"خرید {spec['name']}"),
                    "seo_description": copy.get("description", spec["short"][:160]),
                    "sources": copy.get("sources", ""),
                },
            )
            if not created:
                continue
            added += 1
            if copy:
                ProductSection.objects.create(
                    product=product, kind="benefits", title=copy["heading"], body=copy["body"]
                )
            for order, row in enumerate(spec["specs"]):
                title, value, *key = row
                ProductSpec.objects.create(
                    product=product,
                    title=title,
                    value=value,
                    is_key=bool(key and key[0]),
                    sort_order=order,
                )
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
                self._attach_icon(product, *marks[spec["slug"]])
            self.stdout.write(f"  {product.name}: {product.plans.count()} plans")
        self.stdout.write(
            self.style.SUCCESS(
                f"added {added} of {len(products)} products; the rest were already there"
            )
        )

    def _attach_icon(self, product: Product, url: str | Path, pad: float) -> None:
        target = Path(settings.MEDIA_ROOT) / "products" / f"{product.slug}.svg"
        target.parent.mkdir(parents=True, exist_ok=True)
        body: bytes | None = None
        try:
            if isinstance(url, Path):
                body = url.read_bytes()
            else:
                request = urllib.request.Request(  # noqa: S310  # nosec B310
                    url, headers={"User-Agent": "premshop-seed"}
                )
                with urllib.request.urlopen(  # noqa: S310  # nosec B310
                    request, timeout=15
                ) as response:
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

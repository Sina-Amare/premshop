"""Fill the shop with realistic mock products so the pages can be judged full.

Development only: the owner has no product list yet (open-questions #2), and a
catalog page with one product tells you nothing about how the grid, the
prices, the search or the empty states behave. Idempotent — run it twice and
you get the same shop. `--reset` wipes what it made first.

Images come from Simple Icons on jsDelivr (reachable from Iran, checked) and are
written to MEDIA_ROOT, which is not committed. If the network is unavailable the
product simply has no image, which the cards are designed for.
"""

from __future__ import annotations

import urllib.request
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.catalog.models import Category, Plan, Product, ProductSpec

ICON = "https://cdn.jsdelivr.net/npm/simple-icons@13/icons/{slug}.svg"

T = Product.DeliveryType
R = Product.Region
W = Product.Warranty

CATEGORIES = [
    ("هوش مصنوعی", "ai", "اشتراک ابزارهای هوش مصنوعی، تحویل سریع و با گارانتی."),
    ("ابزار توسعه", "dev", "لایسنس و اشتراک ابزارهای برنامه‌نویسی."),
    ("سرگرمی", "media", "موسیقی و فیلم، روی اکانت خودتان یا اکانت آماده."),
    ("گیفت کارت", "gift", "کارت‌های هدیه با کد، تحویل فوری."),
]

# name, slug, category, icon slug, delivery, region, warranty, hours, short, specs, plans
PRODUCTS: list[dict[str, Any]] = [
    dict(
        name="Claude Pro",
        slug="claude-pro",
        category="ai",
        icon="anthropic",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        region=R.GLOBAL,
        warranty=W.FULL_PERIOD,
        hours=12,
        short="اشتراک کلاود پرو روی اکانت خودتان؛ دسترسی به مدل‌های جدید و محدودیت پیام بالاتر.",
        specs=[
            ("نوع اشتراک", "Pro"),
            ("فعال‌سازی", "روی ایمیل شما"),
            ("پشتیبانی", "تا پایان دوره"),
        ],
        plans=[("۱ ماهه", 30, 900000, 1450000, None), ("۳ ماهه", 90, 2600000, 3900000, 3500000)],
        input="ایمیل حساب کلاود شما",
    ),
    dict(
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        category="ai",
        icon="openai",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        region=R.GLOBAL,
        warranty=W.FULL_PERIOD,
        hours=12,
        short="اشتراک چت‌جی‌پی‌تی پلاس روی اکانت خودتان.",
        specs=[("نوع اشتراک", "Plus"), ("فعال‌سازی", "روی ایمیل شما")],
        plans=[("۱ ماهه", 30, 850000, 1390000, None)],
        input="ایمیل حساب OpenAI شما",
    ),
    dict(
        name="Gemini Advanced",
        slug="gemini-advanced",
        category="ai",
        icon="googlegemini",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        region=R.GLOBAL,
        warranty=W.DAYS_7,
        hours=24,
        short="جمنای ادونسد به همراه ۲ ترابایت فضای گوگل.",
        specs=[("فضای ابری", "۲ ترابایت"), ("فعال‌سازی", "روی جیمیل شما")],
        plans=[("۱ ماهه", 30, 700000, 1190000, None), ("۲ ماهه", 60, 1300000, 2290000, None)],
        input="جیمیل شما",
    ),
    dict(
        name="GitHub Copilot",
        slug="github-copilot",
        category="dev",
        icon="githubcopilot",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        region=R.GLOBAL,
        warranty=W.FULL_PERIOD,
        hours=24,
        short="کوپایلوت روی اکانت گیت‌هاب خودتان؛ برای VS Code و JetBrains.",
        specs=[("پلن", "Individual"), ("محیط‌ها", "VS Code · JetBrains · Neovim")],
        plans=[("۱ ماهه", 30, 550000, 890000, None), ("۱۲ ماهه", 365, 5200000, 8900000, 7900000)],
        input="نام کاربری گیت‌هاب شما",
    ),
    dict(
        name="JetBrains All Products",
        slug="jetbrains-all",
        category="dev",
        icon="jetbrains",
        delivery=T.CODE_LICENSE,
        region=R.GLOBAL,
        warranty=W.FULL_PERIOD,
        hours=48,
        short="لایسنس همه‌ی محصولات جت‌برینز، فعال‌سازی با کد.",
        specs=[("شامل", "IntelliJ · PyCharm · WebStorm · و بقیه"), ("فعال‌سازی", "کد لایسنس")],
        plans=[("۱۲ ماهه", 365, 3900000, 6400000, None)],
    ),
    dict(
        name="Spotify Premium",
        slug="spotify-premium",
        category="media",
        icon="spotify",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        region=R.TURKEY,
        warranty=W.FULL_PERIOD,
        hours=6,
        short="اسپاتیفای پرمیوم روی اکانت خودتان، ریجن ترکیه.",
        specs=[("ریجن", "ترکیه"), ("فعال‌سازی", "روی اکانت شما")],
        plans=[
            ("۱ ماهه", 30, 90000, 190000, None),
            ("۳ ماهه", 90, 250000, 490000, 420000),
            ("۱۲ ماهه", 365, 900000, 1690000, None),
        ],
        input="ایمیل و رمز اسپاتیفای شما",
    ),
    dict(
        name="YouTube Premium",
        slug="youtube-premium",
        category="media",
        icon="youtube",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        region=R.TURKEY,
        warranty=W.DAYS_7,
        hours=12,
        short="یوتیوب پرمیوم بدون تبلیغ، به همراه یوتیوب موزیک.",
        specs=[("ریجن", "ترکیه"), ("شامل", "YouTube Music")],
        plans=[("۱ ماهه", 30, 120000, 240000, None)],
        input="جیمیل شما",
    ),
    dict(
        name="Netflix",
        slug="netflix",
        category="media",
        icon="netflix",
        delivery=T.READY_ACCOUNT,
        region=R.TURKEY,
        warranty=W.FULL_PERIOD,
        hours=2,
        short="اکانت آماده‌ی نتفلیکس، یک صفحه‌ی اختصاصی.",
        specs=[("پلن", "Standard"), ("کیفیت", "Full HD"), ("صفحه", "۱ اختصاصی")],
        plans=[("۱ ماهه", 30, 200000, 390000, 340000)],
    ),
    dict(
        name="Apple Gift Card",
        slug="apple-gift-card",
        category="gift",
        icon="apple",
        delivery=T.GIFT_CARD,
        region=R.US,
        warranty=W.NONE,
        hours=1,
        short="گیفت کارت اپل ریجن آمریکا، تحویل کد.",
        specs=[("ریجن", "آمریکا"), ("تحویل", "کد ۱۶ رقمی")],
        plans=[
            ("۱۰ دلاری", None, 1050000, 1180000, None),
            ("۲۵ دلاری", None, 2600000, 2900000, None),
        ],
    ),
    dict(
        name="Google Play Gift Card",
        slug="google-play-gift-card",
        category="gift",
        icon="googleplay",
        delivery=T.GIFT_CARD,
        region=R.US,
        warranty=W.NONE,
        hours=1,
        short="گیفت کارت گوگل پلی ریجن آمریکا.",
        specs=[("ریجن", "آمریکا")],
        plans=[("۱۰ دلاری", None, 1050000, 1180000, None)],
    ),
    # One draft and one unavailable, so "hidden from visitors" can be seen to hold.
    dict(
        name="Midjourney",
        slug="midjourney",
        category="ai",
        icon="midjourney",
        delivery=T.READY_ACCOUNT,
        region=R.GLOBAL,
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
        category="dev",
        icon="adobe",
        delivery=T.ON_CUSTOMER_ACCOUNT,
        region=R.GLOBAL,
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
                    "region": spec["region"],
                    "warranty": spec["warranty"],
                    "delivery_hours": spec["hours"],
                    "status": spec.get("status", Product.Status.ACTIVE),
                    "seo_title": f"خرید {spec['name']}",
                    "seo_description": spec["short"][:160],
                },
            )
            product.specs.all().delete()
            for order, (title, value) in enumerate(spec["specs"]):
                ProductSpec.objects.create(
                    product=product, title=title, value=value, sort_order=order
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
                    sort_order=order,
                )
            if not options["no_images"] and not product.image:
                self._attach_icon(product, spec["icon"])
            self.stdout.write(f"  {product.name}: {product.plans.count()} plans")
        self.stdout.write(
            self.style.SUCCESS(f"seeded {len(PRODUCTS)} products in {len(CATEGORIES)} categories")
        )

    def _attach_icon(self, product: Product, icon: str) -> None:
        target = Path(settings.MEDIA_ROOT) / "products" / f"{product.slug}.svg"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            try:
                with urllib.request.urlopen(  # noqa: S310 — fixed https constant
                    ICON.format(slug=icon), timeout=15
                ) as response:
                    target.write_bytes(response.read())
            except Exception as exc:  # noqa: BLE001 — no image is a designed state, not a failure
                self.stderr.write(f"  no icon for {product.name}: {type(exc).__name__}")
                return
        product.image.name = f"products/{product.slug}.svg"
        product.save(update_fields=["image"])

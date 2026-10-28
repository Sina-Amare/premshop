"""Public catalogue metadata without prices or unverified business claims."""

from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse

from apps.catalog.models import Product
from apps.core import support

PERSIAN_NAMES = {
    "ChatGPT Plus": "چت جی‌پی‌تی پلاس",
    "Gemini Advanced": "جمینای ادونسد",
    "Google AI Pro": "گوگل ای‌آی پرو",
    "Claude Pro": "کلود پرو",
    "Spotify Premium": "اسپاتیفای پریمیوم",
    "GitHub Copilot": "گیت‌هاب کوپایلت",
}


def website_schema() -> dict:
    root = settings.SITE_URL.rstrip("/") + "/"
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": root + "#organization",
                "name": "PremShop",
                "alternateName": "پرم‌شاپ",
                "url": root,
                "logo": root + "static/img/apple-touch-icon.png",
                "contactPoint": {
                    "@type": "ContactPoint",
                    "contactType": "customer support",
                    "url": support.details()["telegram_url"],
                    "availableLanguage": "fa",
                },
            },
            {
                "@type": "WebSite",
                "@id": root + "#website",
                "name": "PremShop",
                "alternateName": "پرم‌شاپ",
                "url": root,
                "inLanguage": "fa-IR",
                "publisher": {"@id": root + "#organization"},
            },
        ],
    }


def breadcrumb_schema(items: list[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": position,
                "name": name,
                "item": settings.SITE_URL.rstrip("/") + path,
            }
            for position, (name, path) in enumerate([("خانه", "/"), *items], start=1)
        ],
    }


def product_metadata(product: Product) -> dict:
    name = product.name
    if persian := PERSIAN_NAMES.get(name):
        name += f" ({persian})"
    image = f"img/share/{product.slug}.png"
    return {
        "product_meta_title": product.seo_title or f"خرید {name}",
        "product_meta_description": product.seo_description or product.short_description,
        "share_image": image if product.is_public and finders.find(image) else "img/share.png",
    }


def product_breadcrumb(product: Product) -> dict:
    return breadcrumb_schema(
        [
            (product.category.name, reverse("category", args=[product.category.slug])),
            (product.name, product.get_absolute_url()),
        ]
    )

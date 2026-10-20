"""Draw the 1200x630 link previews into static/img/.

og-default.png for every page, and og-<slug>.png for each product a visitor can
see, read from the database DATABASE_URL names. A draft gets no file, and a
file left from a product that is no longer public is removed. No price on any
of them: Telegram keeps a preview it has fetched, and prices change.

Not part of the build. Run it by hand when a product's name, logo or kind
changes, or the home page's headline does, look at what it wrote, and commit
the PNGs. It needs the Chromium the browser tests already use:

    .venv/Scripts/python.exe scripts/render_og.py
"""

from __future__ import annotations

import base64
import os
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from playwright.sync_api import sync_playwright  # noqa: E402

from apps.catalog.models import Product  # noqa: E402

STATIC = ROOT / "static"
OUT = STATIC / "img"
WIDTH, HEIGHT = 1200, 630

HEADLINE = "اشتراک دلاری، پرداخت تومانی"
LEAD = "اشتراک و اکانت سرویس‌های خارجی"

# What the product is, above its name, and how it is bought, under it.
KINDS = {
    Product.DeliveryType.ON_CUSTOMER_ACCOUNT: ("اشتراک", "روی اکانت خودتان، با پرداخت تومانی"),
    Product.DeliveryType.READY_ACCOUNT: ("اشتراک", "اکانت آماده، با پرداخت تومانی"),
    Product.DeliveryType.CODE_LICENSE: ("لایسنس", "تحویل کد، با پرداخت تومانی"),
    Product.DeliveryType.GIFT_CARD: ("گیفت کارت", "تحویل کد، با پرداخت تومانی"),
}
IMAGE_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def font(name: str, weight: int) -> str:
    src = data_uri(STATIC / "fonts" / f"{name}.woff2", "font/woff2")
    family = name.split("-")[0]
    return f'@font-face {{ font-family: {family}; font-weight: {weight}; src: url({src}) format("woff2"); }}'


MARK = re.search(r"<path[^>]*/>", (STATIC / "brand" / "premshop.svg").read_text(encoding="utf-8"))[
    0
]
HERO = (ROOT / "templates" / "svg" / "hero.svg").read_text(encoding="utf-8").strip()

# The page's own tokens as hex: a capture loads no stylesheet. The tile rules are
# input.css's, with the glint at rest.
STYLE = f"""
{font("Estedad-ExtraBold", 800)}
{font("Estedad-SemiBold", 600)}
{font("Vazirmatn-Regular", 400)}
html, body {{ margin: 0; }}
body {{ inline-size: {WIDTH}px; block-size: {HEIGHT}px; overflow: hidden; display: grid;
       grid-template-columns: 660px 540px; background: #F6F4EF; color: #1C1A17; font-family: Vazirmatn; }}
.text {{ display: flex; flex-direction: column; justify-content: center; gap: 28px;
        padding-inline: 80px 48px; min-inline-size: 0; }}
.brand {{ display: flex; align-items: center; gap: 14px; font-family: Estedad; font-weight: 800; font-size: 40px; }}
.brand svg {{ inline-size: 48px; block-size: 48px; fill: currentColor; }}
.rule {{ inline-size: 120px; border-block-start: 4px solid #233D86; }}
h1 {{ margin: 0; font-family: Estedad; font-weight: 800; font-size: 88px; line-height: 1.2; }}
.line {{ margin: 0; font-size: 30px; line-height: 1.6; color: #46423B; }}
.product .brand {{ font-size: 36px; }}
.product .brand svg {{ inline-size: 44px; block-size: 44px; }}
.product h1 {{ font-size: 84px; line-height: 1.1; white-space: nowrap; text-wrap: balance; }}
.logo {{ inline-size: 88px; block-size: 88px; object-fit: contain; }}
.kicker {{ margin: 0; font-family: Estedad; font-weight: 600; font-size: 44px; line-height: 1.2; color: #46423B; }}
.art {{ position: relative; overflow: hidden; }}
.art svg {{ position: absolute; inset-block-start: 0; inset-inline-start: 0;
           inline-size: calc(12 * 315px); block-size: {HEIGHT}px; }}
.tile-k {{ fill: none; stroke: #233D86; stroke-width: 2; stroke-linejoin: round; vector-effect: non-scaling-stroke; }}
.tile-i {{ fill: none; stroke: #233D86; stroke-width: 1; vector-effect: non-scaling-stroke; }}
.tile-d {{ fill: none; stroke: #233D86; stroke-width: 1; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; }}
.tile-p {{ fill: #F6F4EF; }}
.tile-t {{ fill: #E3F0EE; }}
.tile-g {{ fill: #34A7A9; }}
.tile-glint {{ opacity: 0; }}
"""

# A long name steps down from 84px to 64px on one line, then wraps.
FIT = """() => {
  const title = document.querySelector('.product h1');
  if (!title) return;
  for (let size = 84; title.scrollWidth > title.clientWidth && size > 64; ) {
    size -= 4;
    title.style.fontSize = size + 'px';
  }
  if (title.scrollWidth > title.clientWidth) title.style.whiteSpace = 'normal';
}"""


def page(body_class: str, text: str) -> str:
    brand = f'<div class="brand"><svg viewBox="0 0 32 32" aria-hidden="true">{MARK}</svg><bdi>PremShop</bdi></div>'
    return (
        f'<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><style>{STYLE}</style></head>'
        f'<body class="{body_class}"><div class="text">{brand}<div class="rule"></div>{text}</div>'
        f'<div class="art">{HERO}</div></body></html>'
    )


def default_page() -> str:
    return page("", f'<h1>{HEADLINE}</h1><p class="line">{LEAD}</p>')


def product_page(product: Product) -> str:
    kicker, line = KINDS[product.delivery_type]
    logo = ""
    if product.image and Path(product.image.path).is_file():
        path = Path(product.image.path)
        logo = f'<img class="logo" src="{data_uri(path, IMAGE_TYPES[path.suffix.lower()])}" alt="">'
    return page(
        "product",
        f'{logo}<div><p class="kicker">{kicker}</p><h1><bdi>{escape(product.name)}</bdi></h1></div>'
        f'<p class="line">{line}</p>',
    )


def previews() -> dict[str, str]:
    """File name to page: the default, and each product a visitor can see."""
    shots = {"og-default.png": default_page()}
    for product in Product.objects.public().order_by("slug"):
        shots[f"og-{product.slug}.png"] = product_page(product)
    return shots


def main() -> None:
    shots = previews()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        tab = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
        for name, html in shots.items():
            tab.set_content(html)
            # Rejects if a face fails to decode, rather than drawing the fallback.
            tab.evaluate("Promise.all([...document.fonts].map(face => face.load()))")
            tab.evaluate(FIT)
            if tab.evaluate("document.querySelector('.text').scrollHeight") > HEIGHT:
                raise SystemExit(f"{name}: the text runs past the image's foot")
            tab.screenshot(path=OUT / name)
            print(f"wrote {name}")
        browser.close()

    for stale in sorted(set(path.name for path in OUT.glob("og-*.png")) - set(shots)):
        (OUT / stale).unlink()
        print(f"removed {stale}")


if __name__ == "__main__":
    main()

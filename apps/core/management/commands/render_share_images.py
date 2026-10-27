"""Draw the link-preview card and the favicons into static/img/, from the brand SVGs.

Not part of the build. Run it by hand when the mark, the fonts or the home
page's headline change, look at what it wrote, and commit the PNGs. It needs
Playwright's Chromium, which the browser tests already use:

    .venv/Scripts/python.exe manage.py render_share_images

PNG rather than SVG because Telegram announces no preview format beyond the
ones every crawler reads, and Google Search does not take an SVG favicon.
"""

from __future__ import annotations

import base64
import mimetypes
from html import escape
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from apps.catalog.models import Product

STATIC = Path(settings.BASE_DIR) / "static"
OUT = STATIC / "img"

# The home page's headline and lead, as the page prints them today.
HEADLINE = "ChatGPT رو پای کار بیارید."
LEAD = "ChatGPT و Gemini؛ برای کار، تحقیق و یادگیری."


def _font(name: str) -> str:
    data = base64.b64encode((STATIC / "fonts" / name).read_bytes()).decode()
    return f'url(data:font/woff2;base64,{data}) format("woff2")'


def _card(product: Product | None = None) -> str:
    mark = (STATIC / "brand" / "premshop.svg").read_text(encoding="utf-8")
    title = product.name if product else HEADLINE
    lead = product.short_description if product else LEAD
    artwork = ""
    if product:
        if product.image and Path(product.image.path).is_file():
            mime = mimetypes.guess_type(product.image.path)[0] or "application/octet-stream"
            pixels = base64.b64encode(Path(product.image.path).read_bytes()).decode()
            artwork = f'<div class="art"><img src="data:{mime};base64,{pixels}" alt=""></div>'
        else:
            artwork = f'<div class="art">{escape(title[:1])}</div>'
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><style>
@font-face {{ font-family: Estedad; font-weight: 600; src: {_font("Estedad-SemiBold.woff2")}; }}
@font-face {{ font-family: Vazirmatn; font-weight: 400; src: {_font("Vazirmatn-Regular.woff2")}; }}
html, body {{ margin: 0; }}
body {{ inline-size: 1200px; block-size: 630px; display: flex; flex-direction: column;
        background: #f5f6f8; color: #20252e; font-family: Vazirmatn; }}
main {{ flex: 1; min-height: 0; display: flex; align-items: center; gap: 48px;
        padding: 0 96px 8px; }}
h1 {{ font-family: Estedad; font-weight: 600; font-size: {64 if product else 76}px; line-height: 1.5; margin: 0;
      overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }}
.copy {{ flex: 1; min-width: 0; }}
.art {{ width: 176px; height: 176px; flex: 0 0 176px; display: grid; place-items: center;
        background: white; border: 1px solid #dfe3e9; border-radius: 18px; font-size: 76px; color: #2459d3; }}
.art img {{ width: 112px; height: 112px; object-fit: contain; }}
p {{ font-size: {28 if product else 34}px; line-height: 1.8; color: #566170; margin: 24px 0 0; max-inline-size: 24em;
     text-wrap: balance; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }}
footer {{ block-size: 128px; flex-shrink: 0; display: flex; align-items: center; gap: 20px; padding: 0 96px;
          background: #20252e; color: rgba(255, 255, 255, 0.94); }}
footer svg {{ inline-size: 50px; block-size: 50px; }}
footer span {{ font-family: Estedad; font-weight: 600; font-size: 44px; }}
</style></head><body>
<main><div class="copy"><h1>{escape(title)}</h1><p>{escape(lead)}</p></div>{artwork}</main>
<footer>{mark}<span>PremShop</span></footer>
</body></html>"""


def _icon(size: int, *, square: bool = False) -> str:
    svg = (STATIC / "icon.svg").read_text(encoding="utf-8")
    if square:
        # iOS rounds the corners itself and paints a transparent one black.
        if 'rx="14"' not in svg:
            raise CommandError('icon.svg no longer has the rx="14" corner this squares off')
        svg = svg.replace('rx="14"', 'rx="0"')
    return (
        "<!doctype html><html><head><style>html, body { margin: 0; background: transparent; }"
        f"svg {{ display: block; inline-size: {size}px; block-size: {size}px; }}</style>"
        f"</head><body>{svg}</body></html>"
    )


class Command(BaseCommand):
    help = "Draw static/img/share.png and the favicons from the brand SVGs."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--products", action="store_true", help="Also draw public product cards."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from playwright.sync_api import sync_playwright

        OUT.mkdir(exist_ok=True)
        shots = [
            ("share.png", _card(), 1200, 630),
            ("favicon-32.png", _icon(32), 32, 32),
            ("favicon-48.png", _icon(48), 48, 48),
            ("apple-touch-icon.png", _icon(180, square=True), 180, 180),
        ]
        if options["products"]:
            (OUT / "share").mkdir(exist_ok=True)
            shots.extend(
                (f"share/{product.slug}.png", _card(product), 1200, 630)
                for product in Product.objects.public().order_by("pk")
            )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(device_scale_factor=1)
            for name, html, width, height in shots:
                page.set_viewport_size({"width": width, "height": height})
                page.set_content(html)
                page.evaluate("document.fonts.ready.then(() => true)")
                missing = page.evaluate(
                    "[...document.fonts].filter(f => f.status !== 'loaded').map(f => f.family)"
                )
                if missing:
                    raise CommandError(f"{name}: fonts did not load: {missing}")
                page.screenshot(path=OUT / name, omit_background=name != "share.png")
            browser.close()

        # One file, three sizes, for whatever asks for /favicon.ico by name.
        icon = OUT / "favicon-48.png"
        with Image.open(icon) as image:
            image.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
        icon.unlink()
        for name in ("share.png", "favicon-32.png", "apple-touch-icon.png", "favicon.ico"):
            with Image.open(OUT / name) as image:
                self.stdout.write(f"{name}: {image.size[0]}x{image.size[1]}")

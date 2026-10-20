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
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

STATIC = Path(settings.BASE_DIR) / "static"
OUT = STATIC / "img"

# The home page's headline and lead, as the page prints them today.
HEADLINE = "اشتراک دلاری، پرداخت تومانی"
LEAD = "اشتراک را روی اکانت خودتان فعال می‌کنیم یا اکانت و کد آماده تحویل می‌دهیم."


def _font(name: str) -> str:
    data = base64.b64encode((STATIC / "fonts" / name).read_bytes()).decode()
    return f'url(data:font/woff2;base64,{data}) format("woff2")'


def _card() -> str:
    mark = (STATIC / "brand" / "premshop.svg").read_text(encoding="utf-8")
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><style>
@font-face {{ font-family: Estedad; font-weight: 600; src: {_font("Estedad-SemiBold.woff2")}; }}
@font-face {{ font-family: Vazirmatn; font-weight: 400; src: {_font("Vazirmatn-Regular.woff2")}; }}
html, body {{ margin: 0; }}
body {{ inline-size: 1200px; block-size: 630px; display: flex; flex-direction: column;
        background: #ffffff; color: #171717; font-family: Vazirmatn; }}
main {{ flex: 1; display: flex; flex-direction: column; justify-content: center;
        padding: 0 96px 8px; }}
h1 {{ font-family: Estedad; font-weight: 600; font-size: 84px; line-height: 1.3; margin: 0; }}
p {{ font-size: 34px; line-height: 1.8; color: #525252; margin: 24px 0 0; max-inline-size: 24em;
     text-wrap: balance; }}
footer {{ block-size: 128px; display: flex; align-items: center; gap: 20px; padding: 0 96px;
          background: #131313; color: rgba(255, 255, 255, 0.94); }}
footer svg {{ inline-size: 50px; block-size: 50px; }}
footer span {{ font-family: Estedad; font-weight: 600; font-size: 44px; }}
</style></head><body>
<main><h1>{HEADLINE}</h1><p>{LEAD}</p></main>
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

    def handle(self, *args: Any, **options: Any) -> None:
        from playwright.sync_api import sync_playwright

        OUT.mkdir(exist_ok=True)
        shots = [
            ("share.png", _card(), 1200, 630),
            ("favicon-32.png", _icon(32), 32, 32),
            ("favicon-48.png", _icon(48), 48, 48),
            ("apple-touch-icon.png", _icon(180, square=True), 180, 180),
        ]
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

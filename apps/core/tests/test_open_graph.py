"""The link previews a crawler fetches for og:image, drawn by scripts/render_og.py.

Telegram, where many customers arrive from, crops or drops a preview of another
size, and the script is run by hand, so its output is checked here, on disk.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from django.conf import settings
from PIL import Image

from apps.catalog.models import Category, Product

ROOT = Path(settings.BASE_DIR)
IMG = ROOT / "static" / "img"


def test_every_preview_is_a_1200_by_630_png():
    previews = sorted(IMG.glob("og-*.png"))

    assert IMG / "og-default.png" in previews, "the default preview is missing"
    for path in previews:
        with Image.open(path) as image:
            assert (image.format, image.size) == ("PNG", (1200, 630)), path.name


@pytest.mark.django_db
def test_a_draft_gets_no_preview():
    """A file in static/ is public at a guessable address, whatever the page says."""
    spec = importlib.util.spec_from_file_location("render_og", ROOT / "scripts" / "render_og.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    kind = Product.DeliveryType.ON_CUSTOMER_ACCOUNT
    for slug, status in [
        ("claude-pro", "active"),
        ("chatgpt-plus", "draft"),
        ("adobe-cc", "unavailable"),
    ]:
        Product.objects.create(
            category=category, name=slug, slug=slug, delivery_type=kind, status=status
        )

    assert set(script.previews()) == {"og-default.png", "og-claude-pro.png"}

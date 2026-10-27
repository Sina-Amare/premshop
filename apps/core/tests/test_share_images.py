import pytest
from PIL import Image

from apps.catalog.models import Product
from apps.core.management.commands.render_share_images import _card


@pytest.mark.parametrize("suffix,mime", [("svg", "image/svg+xml"), ("png", "image/png")])
def test_share_renderer_embeds_existing_artwork_and_escapes_editor_text(
    settings, tmp_path, suffix, mime
):
    settings.MEDIA_ROOT = tmp_path
    image = tmp_path / ("example." + suffix)
    if suffix == "svg":
        image.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>',
            encoding="utf-8",
        )
    else:
        Image.new("RGB", (10, 10), "white").save(image)
    product = Product(
        name="Example <script>bad</script>", short_description='A & B "quoted"', image=image.name
    )
    html = _card(product)
    assert f"data:{mime};base64," in html
    assert "<script>bad</script>" not in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "A &amp; B &quot;quoted&quot;" in html

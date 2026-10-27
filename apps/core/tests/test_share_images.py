import pytest
from django.utils.html import strip_tags
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
    assert "&lt;script&gt;bad&lt;/script&gt;" in strip_tags(html)
    assert "A &amp; B &quot;quoted&quot;" in strip_tags(html)


def test_share_text_isolates_each_latin_product_name():
    html = _card(
        Product(
            name="JetBrains", short_description="برای Python در PyCharm و Java در IntelliJ IDEA"
        )
    )
    assert "<bdi>PyCharm</bdi>" in html
    assert "<bdi>IntelliJ IDEA</bdi>" in html


@pytest.mark.browser
def test_long_share_text_keeps_its_column_and_footer(browser):
    product = Product(name="W" * 150, short_description="شرح طولانی محصول " * 17)
    page = browser.new_page(viewport={"width": 1200, "height": 630})
    try:
        page.set_content(_card(product))
        page.evaluate("() => document.fonts.ready")
        layout = page.evaluate("""() => {
            const h=document.querySelector('h1'), p=document.querySelector('p'),
                main=document.querySelector('main').getBoundingClientRect(),
                footer=document.querySelector('footer').getBoundingClientRect(),
                art=document.querySelector('.art').getBoundingClientRect(),
                text=document.querySelector('.copy').getBoundingClientRect();
            return {overflow:document.documentElement.scrollWidth>1200,
                footerBottom:footer.bottom, footerTop:footer.top,
                mainBottom:main.bottom, textBottom:text.bottom,
                overlaps:art.right>text.left,
                titleHeight:h.getBoundingClientRect().height,
                descriptionHeight:p.getBoundingClientRect().height};
        }""")
        assert not layout["overflow"] and not layout["overlaps"], layout
        assert layout["footerBottom"] <= 630 and layout["textBottom"] <= layout["footerTop"], layout
    finally:
        page.close()

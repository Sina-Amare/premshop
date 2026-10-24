from django.urls import reverse


def test_shared_shell_names_the_brand_and_keeps_real_destinations(client, db):
    response = client.get(reverse("home"))
    body = response.content.decode()
    assert "اشتراک بعدی‌تون" in body
    assert "پلن مناسب‌تون رو انتخاب کنید" in body
    assert 'class="v-search"' in body
    assert 'href="/faq/"' in body
    assert 'name="q"' in body
    assert "storefront/icon.html" not in body
    assert "/preview-assets/" not in body
    assert "آزمایش ریل" not in body


def test_delivery_facts_are_text_with_an_explicit_details_link(client, db):
    from apps.catalog.models import Category, Product

    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category, name="ChatGPT Plus", slug="chatgpt-plus", status="active"
    )
    body = client.get(product.get_absolute_url()).content.decode()
    assert 'class="v-delivery-details" href="#delivery"' in body
    assert '<a href="#delivery">تا' not in body
    assert 'id="delivery"' in body

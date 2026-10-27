from django.urls import reverse


def test_shared_shell_names_the_brand_and_keeps_real_destinations(client, db):
    response = client.get(reverse("home"))
    body = response.content.decode()
    assert "پای کار بیارید." in body
    assert "از اشتراکتون کار بکشید." in body
    assert 'class="v-search"' in body
    assert 'href="/faq/"' in body
    assert 'name="q"' in body
    assert "storefront/icon.html" not in body
    assert "/preview-assets/" not in body
    assert "آزمایش ریل" not in body


def test_delivery_facts_and_answer_remain_without_a_jump_link(client, db):
    from apps.catalog.models import Category, Product

    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category, name="ChatGPT Plus", slug="chatgpt-plus", status="active"
    )
    body = client.get(product.get_absolute_url()).content.decode()
    assert 'class="v-delivery-details"' not in body
    assert '<a href="#delivery">تا' not in body
    assert 'id="delivery"' in body


def test_orbit_interleaves_complete_public_catalog_without_extra_queries(client, db):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.catalog.models import Category, Product

    categories = [
        Category.objects.create(name=f"گروه {i}", slug=f"group-{i}", sort_order=i) for i in range(3)
    ]
    expected = []
    for i in range(10):
        for category in categories:
            expected.append(
                Product.objects.create(
                    category=category,
                    name=f"Service {category.pk}-{i}",
                    slug=f"service-{category.pk}-{i}",
                    status="active",
                ).pk
            )
    Product.objects.create(category=categories[0], name="Draft", slug="draft", status="draft")
    Product.objects.create(
        category=categories[0], name="Unavailable", slug="unavailable", status="unavailable"
    )
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("home"))
    assert [p.pk for p in response.context["shelf"]] == expected
    assert len(queries) == 5
    body = response.content.decode()
    assert body.count('class="nebula__item"') == 30
    assert "data-orbit-next" in body
    assert "data-orbit-range" in body

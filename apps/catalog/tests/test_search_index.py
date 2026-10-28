import json

import pytest
from django.db import connection

from apps.catalog.models import Category, Product


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["claude", "claudde"])
def test_normalized_and_fuzzy_search_can_use_existing_index(query):
    category = Category.objects.create(name="ابزار", slug="tools")
    product = Product.objects.create(
        category=category, name="Claude Pro", slug="claude", status="active"
    )
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
    results = Product.objects.public().search(query)
    assert list(results) == [product]
    plan = json.loads(results.explain(format="json"))
    assert "product_search_text_trgm" in json.dumps(plan), plan


@pytest.mark.django_db
def test_fuzzy_candidate_filter_is_broader_than_final_acceptance():
    with connection.cursor() as cursor:
        cursor.execute("SHOW pg_trgm.word_similarity_threshold")
        assert float(cursor.fetchone()[0]) == 0.3

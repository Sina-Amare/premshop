"""Trigram search, so a spelling nobody listed still finds the product.

`Product.aliases` is the operator's list of spellings a customer might type,
and it can never be finished: «چت جیپت» was on it and «چت جپت» was not, so
one real spelling of ChatGPT found the product and the other found nothing.
A trigram compares the shapes of the letters rather than the words, which is
the only thing that catches a transliteration nobody thought of.

pg_trgm is trusted from PostgreSQL 13, so `CREATE EXTENSION` needs CREATE on
the database rather than a superuser — checked against the project's own
unprivileged `premshop` role on 15.4, and against the CI image, where
POSTGRES_USER is the superuser anyway.

The GIN index does nothing at a dozen products; a sequential scan over them
is instant either way. It is here because the index is what makes this
approach still correct at a thousand, and adding it later means remembering
that it was missing.
"""

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("catalog", "0005_productspec_is_key")]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="product",
            index=GinIndex(
                fields=["search_text"],
                opclasses=["gin_trgm_ops"],
                name="product_search_text_trgm",
            ),
        ),
    ]

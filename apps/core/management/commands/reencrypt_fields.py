"""Re-encrypt stored fields under the active field key, in locked batches."""

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection, transaction
from psycopg import sql

from apps.core.fields import EncryptedTextField, field_keys


class Command(BaseCommand):
    help = "Rotate encrypted fields without printing their values."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("Batch size must be positive.")
        current, _ = field_keys()
        rotated = 0
        for model in apps.get_models():
            for field in model._meta.fields:
                if not isinstance(field, EncryptedTextField):
                    continue
                query = (
                    sql.SQL(
                        "SELECT {pk}, {column} FROM {table} WHERE {pk} > %s "
                        "AND {column} <> '' ORDER BY {pk} LIMIT %s FOR UPDATE"
                    )
                    .format(
                        table=sql.Identifier(model._meta.db_table),
                        pk=sql.Identifier(model._meta.pk.attname),
                        column=sql.Identifier(field.attname),
                    )
                    .as_string()
                )
                last_pk = 0
                while True:
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(query, [last_pk, batch_size])
                        rows = cursor.fetchall()
                        for row_pk, stored in rows:
                            if not stored.startswith(current + "$"):
                                value = field.from_db_value(stored, None, connection)
                                model._default_manager.filter(pk=row_pk).update(
                                    **{field.name: value}
                                )
                                rotated += 1
                        if rows:
                            last_pk = rows[-1][0]
                    if len(rows) < batch_size:
                        break
        self.stdout.write(f"Rotated {rotated} encrypted values.")

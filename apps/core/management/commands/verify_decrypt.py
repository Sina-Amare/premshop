"""Decrypt every stored encrypted value and report counts only (ADR-0007).

A restore that cannot decrypt is a failed restore, so `scripts/deploy.py restore`
runs this before the restored site opens.
"""

from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection
from psycopg import sql

from apps.core.fields import EncryptedTextField, field_keys


class Command(BaseCommand):
    help = "Check that every encrypted value decrypts, printing counts and never a value."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--allow-empty",
            action="store_true",
            help="Pass when no value is encrypted at all, as before any customer input.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Where nothing decrypts, only this, compared with the fingerprint kept
        # beside the escrowed key, shows the site will write under that key.
        current, _ = field_keys()
        self.stdout.write(f"Field encryption key fingerprint: {current}")
        total = failed = 0
        for model in apps.get_models():
            for field in model._meta.fields:
                if not isinstance(field, EncryptedTextField):
                    continue
                query = sql.SQL("SELECT {column} FROM {table} WHERE {column} <> ''").format(
                    table=sql.Identifier(model._meta.db_table),
                    column=sql.Identifier(field.attname),
                )
                decrypted = unreadable = 0
                with connection.chunked_cursor() as cursor:
                    cursor.execute(query.as_string())
                    while rows := cursor.fetchmany(500):
                        for (stored,) in rows:
                            try:
                                field.from_db_value(stored, None, connection)
                                decrypted += 1
                            except ImproperlyConfigured:
                                unreadable += 1
                self.stdout.write(
                    f"{model._meta.label}.{field.name}: {decrypted} decrypted, {unreadable} failed"
                )
                total += decrypted + unreadable
                failed += unreadable
        if failed:
            raise CommandError(f"{failed} of {total} encrypted values cannot be decrypted.")
        if not total and not options["allow_empty"]:
            raise CommandError(
                "No encrypted value was found, so nothing proves the key; "
                "--allow-empty passes a database that has none yet."
            )
        self.stdout.write(f"All {total} encrypted values decrypt.")

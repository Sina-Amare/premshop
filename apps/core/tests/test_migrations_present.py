"""Every project app with models has migrations.

`makemigrations --check` in the gate proposes a first migration only for an app that
already has a migrations package, so a new app written by hand passed it, and pytest
built its tables anyway with run_syncdb.
"""

from django.apps import apps
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings


def unmigrated_project_apps() -> set[str]:
    with_models = {
        config.label
        for config in apps.get_app_configs()
        if config.name.startswith("apps.") and list(config.get_models())
    }
    return with_models & MigrationLoader(None, ignore_no_migrations=True).unmigrated_apps


def test_every_project_app_with_models_has_migrations():
    assert unmigrated_project_apps() == set()


@override_settings(MIGRATION_MODULES={"catalog": None})
def test_an_app_without_migrations_is_found():
    assert unmigrated_project_apps() == {"catalog"}

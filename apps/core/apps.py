from django.apps import AppConfig
from django.contrib.staticfiles.apps import StaticFilesConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = _("سامانه")


class StaticFiles(StaticFilesConfig):
    # Tailwind's source never reaches a page, and its @import names an npm package
    # that manifest storage cannot hash; app.css is the file pages load.
    ignore_patterns = [*StaticFilesConfig.ignore_patterns, "input.css"]

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orders"
    verbose_name = _("فروشگاه")

    def ready(self) -> None:
        from apps.accounts import telegram
        from apps.orders import telegram_panel

        telegram.register_panel(telegram_panel.render)

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    verbose_name = _("فروشگاه")

    def ready(self) -> None:
        from apps.core import events
        from apps.notifications.services import enqueue_event

        events.register(enqueue_event)

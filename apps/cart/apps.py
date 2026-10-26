from django.apps import AppConfig


class CartConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cart"

    def ready(self) -> None:
        from apps.cart import signals  # noqa: F401

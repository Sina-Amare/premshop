from typing import Any

from django.core.management.base import BaseCommand

from apps.cart.services import purge_guest_carts


class Command(BaseCommand):
    help = "Delete guest carts inactive for more than 30 days."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(f"Deleted {purge_guest_carts()} stale guest carts.")

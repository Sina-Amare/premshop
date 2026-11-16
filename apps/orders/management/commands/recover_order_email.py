from getpass import getpass
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.models import User
from apps.orders import proof
from apps.orders.models import Order


class Command(BaseCommand):
    help = "Recover an order's reveal email through its pre-purchase Telegram link."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("order_number", type=int)
        parser.add_argument("--operator-id", type=int, required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            actor = User.objects.get(pk=options["operator_id"])
            order = Order.objects.get(order_number=options["order_number"])
            arguments = {"order_id": order.pk, "actor": actor, "email": options["email"]}
            if options["confirm"]:
                if not proof.finish_recovery(**arguments, code=getpass("Recovery code: ")):
                    raise CommandError("Recovery code invalid or expired.")
                self.stdout.write(
                    "Recovery recorded. The customer must now confirm the email code."
                )
            else:
                proof.start_recovery(**arguments)
                self.stdout.write("Challenge sent through the shop's bot. Rerun with --confirm.")
        except PermissionDenied, ValidationError, User.DoesNotExist, Order.DoesNotExist:
            raise CommandError("Recovery refused; no address changed.") from None

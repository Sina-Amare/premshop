import logging
from collections.abc import Callable
from datetime import datetime

from django.db import transaction

logger = logging.getLogger(__name__)
_consumer: Callable[..., object] | None = None


def register(consumer: Callable[..., object]) -> None:
    global _consumer
    _consumer = consumer


def emit(
    template_name: str,
    occurrence_id: int,
    order_id: int,
    *,
    item_id: int | None = None,
    due_at: datetime | None = None,
) -> None:
    def enqueue() -> None:
        try:
            if _consumer is None:
                raise RuntimeError("Notification consumer is not registered")
            _consumer(template_name, occurrence_id, order_id, item_id=item_id, due_at=due_at)
        except Exception as error:  # noqa: BLE001 - a notice cannot undo a committed action
            logger.error(
                "notification creation failed order=%s occurrence=%s error=%s",
                order_id,
                occurrence_id,
                type(error).__name__,
            )

    transaction.on_commit(enqueue)

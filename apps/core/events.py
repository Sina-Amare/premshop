import logging
from datetime import datetime

from django.db import transaction

logger = logging.getLogger(__name__)


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
            from apps.notifications.services import enqueue_event

            enqueue_event(template_name, occurrence_id, order_id, item_id=item_id, due_at=due_at)
        except Exception as error:  # noqa: BLE001 - a notice cannot undo a committed action
            logger.error(
                "notification creation failed order=%s occurrence=%s error=%s",
                order_id,
                occurrence_id,
                type(error).__name__,
            )

    transaction.on_commit(enqueue)

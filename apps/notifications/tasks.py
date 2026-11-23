from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.notifications.services import (
    discover_confirmations,
    discover_customer_claims,
    drop_superseded_delay_notices,
    send_due_notifications,
    send_due_telegram,
)


@db_periodic_task(crontab(minute="*"))
def send_order_notifications() -> int:
    discover_confirmations()
    discover_customer_claims()
    drop_superseded_delay_notices()
    return send_due_notifications() + send_due_telegram()

from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.notifications.services import discover_confirmations, send_due_notifications


@db_periodic_task(crontab(minute="*"))
def send_order_notifications() -> int:
    discover_confirmations()
    return send_due_notifications()

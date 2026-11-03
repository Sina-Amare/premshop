from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.notifications.services import discover_confirmations, send_due_confirmations


@db_periodic_task(crontab(minute="*"))
def confirm_new_orders() -> int:
    discover_confirmations()
    return send_due_confirmations()

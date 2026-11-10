from django.conf import settings
from django.core.cache import cache
from huey import crontab
from huey.contrib.djhuey import HUEY, db_periodic_task
from huey.exceptions import TaskLockedException

from apps.accounts import telegram

# Created here, at import, so the consumer's start-up flush knows it: a lock created
# inside the body would survive a killed worker and stop every later poll.
POLL_LOCK = HUEY.lock_task("telegram-updates")


@db_periodic_task(crontab(minute="*"))
def poll_telegram() -> int:
    """One poll window, then the next at once; the minute run restarts a broken chain."""
    try:
        with POLL_LOCK:
            received = telegram.poll()
    except TaskLockedException:
        return 0
    # Queued after the lock is free, and only while Telegram answers: a failed poll
    # waits for the minute run instead of failing again as fast as it can.
    if settings.TELEGRAM_BOT_TOKEN and not cache.get(telegram.DOWN_KEY):
        poll_telegram()
    return received

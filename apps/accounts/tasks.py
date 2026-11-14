import logging
import time

from django.conf import settings
from django.core.cache import cache
from huey import crontab
from huey.contrib.djhuey import HUEY, db_periodic_task, db_task
from huey.exceptions import TaskLockedException

from apps.accounts import otp, telegram, telegram_codes

logger = logging.getLogger(__name__)

# Created here, at import, so the consumer's start-up flush knows it: a lock created
# inside the body would survive a killed worker and stop every later poll.
POLL_LOCK = HUEY.lock_task("telegram-updates")
STARTED_KEY = "telegram:poll-started"
# A poll window is about 55 s, plus at most one call's timeout, so a lock still held
# three minutes after its poll began has no poll behind it.
STUCK_SECONDS = 180


@db_task()
def send_telegram_code(
    phone: str, request_id: str, expires_at: int, generation: otp.TelegramLink | None
) -> None:
    telegram_codes.deliver_code(phone, request_id, expires_at, generation)


@db_task()
def delete_login_message(chat_id: int, message_id: int) -> None:
    telegram_codes.delete_message(chat_id, message_id)


@db_periodic_task(crontab(minute="*"))
def poll_telegram() -> int:
    """One poll window, then the next at once; the minute run restarts a broken chain."""
    try:
        with POLL_LOCK:
            cache.set(STARTED_KEY, time.time(), timeout=None)
            received = telegram.poll()
    except TaskLockedException:
        started = cache.get(STARTED_KEY)
        # A release that failed (the store down as a poll ended) leaves the lock to nobody,
        # and every run would skip in silence until the worker restarts.
        if started is None or time.time() - started > STUCK_SECONDS:
            logger.error("telegram poll lock held with no poll behind it; cleared")
            POLL_LOCK.clear()
        return 0
    # Queued after the lock is free, and only while Telegram answers: a failed poll
    # waits for the minute run instead of failing again as fast as it can.
    if settings.TELEGRAM_BOT_TOKEN and not cache.get(telegram.DOWN_KEY):
        poll_telegram()
    return received

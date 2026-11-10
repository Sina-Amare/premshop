from huey import crontab
from huey.contrib.djhuey import db_periodic_task, lock_task

from apps.accounts import telegram


# The lock is created here, at import, so the consumer's start-up flush knows it: a
# lock created inside the body would survive a killed worker and stop every later poll.
@db_periodic_task(crontab(minute="*"))
@lock_task("telegram-updates")
def poll_telegram() -> int:
    return telegram.poll()

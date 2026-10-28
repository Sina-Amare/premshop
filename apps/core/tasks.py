from django.core.management import call_command
from huey import crontab
from huey.contrib.djhuey import db_periodic_task


@db_periodic_task(crontab(hour="3", minute="10"))
def clean_expired_sessions() -> None:
    call_command("clearsessions", verbosity=0)

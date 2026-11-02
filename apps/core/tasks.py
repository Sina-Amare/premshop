from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task


@db_periodic_task(crontab(hour="3", minute="10"))
def clean_expired_sessions() -> None:
    call_command("clearsessions", verbosity=0)


@db_periodic_task(crontab(hour="3", minute="15"))
def clean_mail_attempts() -> int:
    from apps.core.models import MailAttempt

    deleted, _ = MailAttempt.objects.filter(
        created_at__lt=timezone.now() - timedelta(days=90)
    ).delete()
    return deleted

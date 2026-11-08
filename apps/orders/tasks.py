from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.orders.models import CredentialAccessLog, Order, OrderItem, UnpaidExpirySweep
from apps.orders.services import delete_expired_details, expire_unpaid_orders


@db_periodic_task(crontab(hour="3", minute="25"))
def clean_delivered_details() -> int:
    return delete_expired_details()


@db_periodic_task(crontab(hour="3", minute="20"))
def clean_credential_access_logs() -> int:
    deleted, _ = CredentialAccessLog.objects.filter(
        created_at__lt=timezone.now() - timedelta(days=365)
    ).delete()
    return deleted


@db_periodic_task(crontab(minute="*"))
def sweep_unpaid_orders() -> int:
    processed = expire_unpaid_orders()
    pending = OrderItem.objects.filter(
        order_id=OuterRef("pk"), status=OrderItem.Status.PENDING_PAYMENT
    )
    backlog = (
        Order.objects.filter(unpaid_expires_at__lte=timezone.now()).filter(Exists(pending)).count()
    )
    UnpaidExpirySweep.objects.update_or_create(
        pk=1,
        defaults={
            "last_success_at": timezone.now(),
            "processed_count": processed,
            "overdue_count": backlog,
        },
    )
    return processed

from django.db.models import Exists, OuterRef
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.orders.models import Order, OrderItem, UnpaidExpirySweep
from apps.orders.services import expire_unpaid_orders


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

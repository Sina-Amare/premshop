from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.cart.services import purge_guest_carts


@db_periodic_task(crontab(hour="3", minute="0"))
def clean_guest_carts() -> int:
    return purge_guest_carts()

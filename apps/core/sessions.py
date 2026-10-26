from collections.abc import Iterator
from contextlib import contextmanager

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone


@contextmanager
def login_session(request: HttpRequest) -> Iterator[None]:
    """A login must see guest writes committed since this request loaded its session."""
    # The admin site imports this module before Django loads models.
    from django.contrib.sessions.models import Session

    with transaction.atomic():
        if key := request.session.session_key:
            stored = (
                Session.objects.select_for_update()
                .filter(session_key=key, expire_date__gt=timezone.now())
                .first()
            )
            request.session.clear()
            request.session.update(stored.get_decoded() if stored else {})
        yield

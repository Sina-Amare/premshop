from typing import Any

from django.contrib import messages
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.http import HttpRequest

from apps.accounts.models import User
from apps.cart.services import GUEST_CART_ID, GUEST_PREVIOUS_EXPIRY, merge_carts


@receiver(user_logged_in)
def merge_guest_cart(sender: type[User], request: HttpRequest, user: User, **kwargs: Any) -> None:
    # login() rotates the session key, but preserves this server-side reference.
    if (guest_id := request.session.get(GUEST_CART_ID)) is None:
        return
    reduced = merge_carts(user=user, guest_cart_id=guest_id)
    request.session.pop(GUEST_CART_ID, None)
    request.session.set_expiry(request.session.pop(GUEST_PREVIOUS_EXPIRY, None))
    if reduced:
        messages.info(
            request,
            "تعداد بعضی محصولات با توجه به موجودی و محدودیت خرید تغییر کرد. سبد خریدتان را بررسی کنید.",
        )

"""A cart holds intentions; the catalog supplies every price."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Plan, Product
from apps.catalog.pricing import effective_price
from apps.core.formatting import to_persian_digits

GUEST_CART_ID = "guest_cart_id"
GUEST_PREVIOUS_EXPIRY = "guest_cart_previous_expiry"
GUEST_AGE = 30 * 86400
RESERVATION_AGE = timedelta(minutes=20)


def get_cart(request: HttpRequest) -> Cart | None:
    if request.user.is_authenticated:
        return Cart.objects.filter(user_id=request.user.pk).first()
    if key := request.session.session_key:
        return Cart.objects.filter(session_key=key, user__isnull=True).first()
    return None


def _locked_cart(request: HttpRequest, *, create: bool) -> Cart | None:
    lookup: dict[str, Any]
    if request.user.is_authenticated:
        # Admin audit rows reference this user while holding a stock lock.
        if (
            not User.objects.select_for_update(no_key=True)
            .filter(pk=request.user.pk, is_active=True)
            .first()
        ):
            raise PermissionDenied
        lookup = {"user_id": request.user.pk}
    else:
        if request.session.session_key is None:
            if not create:
                return None
            request.session.save()
        if (
            not Session.objects.select_for_update()
            .filter(session_key=request.session.session_key, expire_date__gt=timezone.now())
            .exists()
        ):
            raise ValidationError("صفحه را دوباره باز کنید و سبد خریدتان را بررسی کنید.")
        lookup = {"session_key": request.session.session_key, "user_id": None}
    carts = Cart.objects.select_for_update()
    if create:
        return carts.get_or_create(**lookup)[0]
    return carts.filter(**lookup).first()


def _touch(request: HttpRequest, cart: Cart) -> None:
    Cart.objects.filter(pk=cart.pk).update(updated_at=timezone.now())
    if cart.user_id is None:
        if GUEST_PREVIOUS_EXPIRY not in request.session:
            request.session[GUEST_PREVIOUS_EXPIRY] = request.session.get("_session_expiry")
        request.session[GUEST_CART_ID] = cart.pk
        request.session.set_expiry(GUEST_AGE)
        request.session.save()


def _available_plan(plan_id: int) -> Plan:
    plan = Plan.objects.filter(
        pk=plan_id, is_available=True, product__in=Product.objects.public()
    ).first()
    if plan is None:
        raise ValidationError("این محصول فعلاً موجود نیست.")
    return plan


def _validate_quantity(plan: Plan, quantity: int) -> None:
    limit = 1 if plan.requires_customer_input else plan.max_quantity
    if type(quantity) is not int or not 1 <= quantity <= limit:
        raise ValidationError(f"حداکثر تعداد برای این محصول: {to_persian_digits(limit)} عدد.")


def _reserved_totals(plans: list[Plan]) -> dict[int, int]:
    limited = [plan.pk for plan in plans if plan.stock_quantity is not None]
    if not limited:
        return {}
    return dict(
        CartItem.objects.filter(plan_id__in=limited, reserved_until__gt=timezone.now())
        .values("plan_id")
        .annotate(total=Sum("quantity"))
        .values_list("plan_id", "total")
    )


def _capacity(plan: Plan, item: CartItem | None, reserved: int) -> int:
    if plan.stock_quantity is None:
        return plan.max_quantity
    own = (
        item.quantity
        if item and item.reserved_until and item.reserved_until > timezone.now()
        else 0
    )
    return max(0, min(plan.max_quantity, plan.stock_quantity - reserved + own))


def _reserve(plan: Plan, item: CartItem | None, quantity: int) -> datetime | None:
    _validate_quantity(plan, quantity)
    if plan.stock_quantity is None:
        return None
    available = _capacity(plan, item, _reserved_totals([plan]).get(plan.pk, 0))
    if quantity > available:
        if not available:
            raise ValidationError("این محصول فعلاً موجود نیست.")
        raise ValidationError(f"فقط {to_persian_digits(available)} عدد از این محصول موجود است.")
    now = timezone.now()
    return (
        item.reserved_until
        if item and item.reserved_until and item.reserved_until > now
        else now + RESERVATION_AGE
    )


@transaction.atomic
def add_to_cart(request: HttpRequest, *, plan_id: int, quantity: int = 1) -> CartItem:
    plan = _available_plan(plan_id)
    _validate_quantity(plan, quantity)
    _reserve(plan, None, quantity)
    cart = cast(Cart, _locked_cart(request, create=True))
    plan = Plan.objects.select_for_update().get(pk=plan_id)
    _available_plan(plan_id)
    item = cart.items.filter(plan_id=plan_id).first()
    new_quantity = quantity + (item.quantity if item else 0)
    reserved_until = _reserve(plan, item, new_quantity)
    if item:
        item.quantity = new_quantity
        item.reserved_until = reserved_until
        item.save(update_fields=["quantity", "reserved_until"])
    else:
        item = CartItem.objects.create(
            cart=cart, plan=plan, quantity=quantity, reserved_until=reserved_until
        )
    _touch(request, cart)
    return item


@transaction.atomic
def set_quantity(request: HttpRequest, *, plan_id: int, quantity: int) -> None:
    if type(quantity) is int and quantity == 0:
        remove_from_cart(request, plan_id=plan_id)
        return
    plan = _available_plan(plan_id)
    _validate_quantity(plan, quantity)
    if cart := _locked_cart(request, create=False):
        plan = Plan.objects.select_for_update().get(pk=plan_id)
        _available_plan(plan_id)
        if item := cart.items.filter(plan_id=plan_id).first():
            reserved_until = _reserve(plan, item, quantity)
            item.quantity = quantity
            item.reserved_until = reserved_until
            item.save(update_fields=["quantity", "reserved_until"])
            _touch(request, cart)


@transaction.atomic
def remove_from_cart(request: HttpRequest, *, plan_id: int) -> None:
    if cart := _locked_cart(request, create=False):
        Plan.objects.select_for_update().filter(pk=plan_id).first()
        if cart.items.filter(plan_id=plan_id).delete()[0]:
            _touch(request, cart)


@transaction.atomic
def merge_carts(*, user: User, guest_cart_id: int) -> bool:
    """Serialize account edits and merges; return whether a quantity was reduced."""
    if not User.objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).first():
        raise PermissionDenied
    carts = list(
        Cart.objects.select_for_update()
        .filter(Q(user_id=user.pk) | Q(pk=guest_cart_id, user__isnull=True))
        .order_by("pk")
    )
    guest = next((cart for cart in carts if cart.user_id is None), None)
    if guest is None:
        return False
    account = next((cart for cart in carts if cart.user_id == user.pk), None)
    guest_items = list(guest.items.order_by("pk"))
    plans = {
        plan.pk: plan
        for plan in Plan.objects.select_for_update()
        .filter(pk__in=[item.plan_id for item in guest_items])
        .order_by("pk")
    }
    reduced = False
    for item in guest_items:
        plan = plans[item.plan_id]
        existing = account.items.filter(plan_id=item.plan_id).first() if account else None
        quantity = item.quantity + (existing.quantity if existing else 0)
        limit = 1 if plan.requires_customer_input else plan.max_quantity
        reduced |= quantity > limit
        quantity = min(quantity, limit)
        deadline = None
        if plan.stock_quantity is not None:
            now = timezone.now()
            live = [
                row
                for row in (item, existing)
                if row and row.reserved_until and row.reserved_until > now
            ]
            own = sum(row.quantity for row in live)
            available = max(0, plan.stock_quantity - _reserved_totals([plan]).get(plan.pk, 0) + own)
            if available and live:
                reduced |= quantity > available
                quantity = min(quantity, available)
                deadline = min(cast(datetime, row.reserved_until) for row in live)
        if existing:
            existing.quantity = quantity
            existing.reserved_until = deadline
            existing.save(update_fields=["quantity", "reserved_until"])
        else:
            item.quantity = quantity
            item.reserved_until = deadline
            if account:
                item.cart = account
            item.save(update_fields=["quantity", "cart", "reserved_until"])
    if account:
        guest.delete()
    else:
        guest.user = user
        guest.session_key = None
        guest.save(update_fields=["user", "session_key"])
        account = guest
    Cart.objects.filter(pk=account.pk).update(updated_at=timezone.now())
    return reduced


def cart_count(request: HttpRequest) -> int:
    if request.user.is_authenticated:
        items = CartItem.objects.filter(cart__user_id=request.user.pk)
    elif key := request.session.session_key:
        items = CartItem.objects.filter(cart__session_key=key, cart__user__isnull=True)
    else:
        return 0
    return items.aggregate(total=Sum("quantity"))["total"] or 0


@dataclass(frozen=True)
class CartLine:
    plan_id: int
    plan: Plan | None
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    unavailable: bool
    quantity_invalid: bool
    max_quantity: int
    reservation_expired: bool
    reservation_seconds: int | None


@dataclass(frozen=True)
class CartSummary:
    lines: list[CartLine]
    total: Decimal

    @property
    def has_purchasable_items(self) -> bool:
        return any(
            not line.unavailable and not line.quantity_invalid and not line.reservation_expired
            for line in self.lines
        )


@dataclass(frozen=True)
class PurchaseOption:
    plan: Plan
    in_cart: int
    max_add: int
    maximum: int
    reserved_until: datetime | None

    @property
    def selectable(self) -> bool:
        return self.plan.is_available and (self.max_add > 0 or self.in_cart > 0)


def purchase_options(request: HttpRequest | None, plans: list[Plan]) -> list[PurchaseOption]:
    cart = get_cart(request) if request else None
    own = {item.plan_id: item for item in cart.items.all()} if cart else {}
    reserved = _reserved_totals(plans)
    options = []
    for plan in plans:
        item = own.get(plan.pk)
        quantity = item.quantity if item else 0
        maximum = _capacity(plan, item, reserved.get(plan.pk, 0)) if plan.is_available else 0
        options.append(
            PurchaseOption(
                plan,
                quantity,
                max(0, maximum - quantity),
                maximum,
                item.reserved_until if item else None,
            )
        )
    return options


def cart_summary(cart: Cart | None) -> CartSummary:
    if cart is None:
        return CartSummary([], Decimal(0))
    items = list(cart.items.order_by("added_at", "pk"))
    # A stored plan id must not make a draft's facts public again.
    plans = {
        plan.pk: plan
        for plan in Plan.objects.select_related("product").filter(
            pk__in=[item.plan_id for item in items], product__in=Product.objects.public()
        )
    }
    reserved = _reserved_totals(list(plans.values()))
    lines = []
    total = Decimal(0)
    for item in items:
        plan = plans.get(item.plan_id)
        capacity = _capacity(plan, item, reserved.get(item.plan_id, 0)) if plan else 0
        unavailable = plan is None or not plan.is_available or capacity == 0
        invalid = bool(plan and item.quantity > capacity)
        expires = bool(
            plan
            and plan.stock_quantity is not None
            and (not item.reserved_until or item.reserved_until <= timezone.now())
        )
        seconds = (
            max(0, int((item.reserved_until - timezone.now()).total_seconds()))
            if plan and plan.stock_quantity is not None and item.reserved_until
            else None
        )
        price = effective_price(plan) if plan else Decimal(0)
        amount = price * item.quantity
        if not unavailable and not invalid and not expires:
            total += amount
        lines.append(
            CartLine(
                item.plan_id,
                plan,
                item.quantity,
                price,
                amount,
                unavailable,
                invalid,
                capacity,
                expires,
                seconds,
            )
        )
    return CartSummary(lines, total)


@transaction.atomic
def purge_guest_carts() -> int:
    cutoff = timezone.now() - timedelta(seconds=GUEST_AGE)
    carts = list(Cart.objects.select_for_update().filter(user__isnull=True, updated_at__lt=cutoff))
    for cart in carts:
        cart.delete()
    return len(carts)

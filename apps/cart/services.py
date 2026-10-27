"""A cart holds intentions; the catalog supplies every price."""

from dataclasses import dataclass
from datetime import timedelta
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
    if type(quantity) is not int or quantity < 1:
        raise ValidationError("حداقل تعداد قابل خرید: ۱ عدد")
    if quantity > limit:
        raise ValidationError(f"حداکثر تعداد قابل خرید: {to_persian_digits(limit)} عدد")


def _capacity(plan: Plan) -> int:
    if not plan.is_in_stock:
        return 0
    if plan.stock_quantity is None:
        return plan.max_quantity
    return min(plan.max_quantity, plan.stock_quantity)


def _check_stock(plan: Plan, quantity: int) -> None:
    _validate_quantity(plan, quantity)
    available = _capacity(plan)
    if quantity > available:
        if not available:
            raise ValidationError("این محصول فعلاً موجود نیست.")
        raise ValidationError(f"موجودی فعلی: {to_persian_digits(available)} عدد")


@transaction.atomic
def add_to_cart(request: HttpRequest, *, plan_id: int, quantity: int = 1) -> CartItem:
    plan = _available_plan(plan_id)
    _validate_quantity(plan, quantity)
    _check_stock(plan, quantity)
    cart = cast(Cart, _locked_cart(request, create=True))
    plan = Plan.objects.select_for_update().get(pk=plan_id)
    _available_plan(plan_id)
    item = cart.items.filter(plan_id=plan_id).first()
    new_quantity = quantity + (item.quantity if item else 0)
    _check_stock(plan, new_quantity)
    if item:
        item.quantity = new_quantity
        item.save(update_fields=["quantity"])
    else:
        item = CartItem.objects.create(cart=cart, plan=plan, quantity=quantity)
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
            _check_stock(plan, quantity)
            item.quantity = quantity
            item.save(update_fields=["quantity"])
            _touch(request, cart)
            return
    raise ValidationError("این مورد دیگر در سبد خرید نیست.")


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
        if existing:
            existing.quantity = quantity
            existing.save(update_fields=["quantity"])
        else:
            item.quantity = quantity
            if account:
                item.cart = account
            item.save(update_fields=["quantity", "cart"])
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

    @property
    def purchasable(self) -> bool:
        return not (self.unavailable or self.quantity_invalid)


@dataclass(frozen=True)
class CartSummary:
    lines: list[CartLine]
    total: Decimal

    @property
    def has_purchasable_items(self) -> bool:
        return any(line.purchasable for line in self.lines)

    @property
    def ready(self) -> bool:
        return bool(self.lines) and all(line.purchasable for line in self.lines)


@dataclass(frozen=True)
class PurchaseOption:
    plan: Plan
    in_cart: int
    max_add: int
    maximum: int

    @property
    def selectable(self) -> bool:
        return self.plan.is_available and self.maximum > 0


def purchase_options(request: HttpRequest | None, plans: list[Plan]) -> list[PurchaseOption]:
    cart = get_cart(request) if request else None
    own = {item.plan_id: item for item in cart.items.all()} if cart else {}
    options = []
    for plan in plans:
        item = own.get(plan.pk)
        quantity = item.quantity if item else 0
        maximum = _capacity(plan)
        options.append(
            PurchaseOption(
                plan,
                quantity,
                max(0, maximum - quantity),
                maximum,
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
    lines = []
    total = Decimal(0)
    for item in items:
        plan = plans.get(item.plan_id)
        capacity = _capacity(plan) if plan else 0
        unavailable = plan is None or not plan.is_available or capacity == 0
        invalid = bool(plan and item.quantity > capacity)
        price = effective_price(plan) if plan else Decimal(0)
        amount = price * item.quantity
        if not unavailable and not invalid:
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

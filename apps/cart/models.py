from django.conf import settings
from django.db import models
from django.db.models import Q


class Cart(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    session_key = models.CharField(  # noqa: DJ001 — account carts have no session owner.
        max_length=40, null=True, unique=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False) & Q(session_key__isnull=True))
                | (Q(user__isnull=True) & Q(session_key__isnull=False) & ~Q(session_key="")),
                name="cart_exactly_one_owner",
            )
        ]
        indexes = [
            models.Index(
                fields=["updated_at"], condition=Q(user__isnull=True), name="cart_guest_age"
            )
        ]

    def __str__(self) -> str:
        return f"Cart {self.pk}"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, related_name="items", on_delete=models.CASCADE)
    plan = models.ForeignKey("catalog.Plan", on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["cart", "plan"], name="cart_one_line_per_plan"),
            models.CheckConstraint(
                condition=Q(quantity__gte=1) & Q(quantity__lte=10), name="cart_quantity_1_to_10"
            ),
        ]

    def __str__(self) -> str:
        return f"Cart item {self.pk}"

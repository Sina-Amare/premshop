from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product


@pytest.fixture
def buyer():
    return User.objects.create_user("buyer@example.test", "CartPassword!2026")


@pytest.fixture
def plan():
    category = Category.objects.create(name="نمونه", slug="sample")
    product = Product.objects.create(
        category=category, name="سرویس نمونه", slug="sample", status=Product.Status.ACTIVE
    )
    return Plan.objects.create(
        product=product,
        title="یک ماهه",
        duration_days=30,
        cost_price=Decimal("100000"),
        sale_price=Decimal("200000"),
        max_quantity=3,
    )

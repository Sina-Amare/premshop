from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product


@pytest.fixture(autouse=True)
def encryption_keys(settings):
    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEY_FALLBACKS = []


@pytest.fixture
def buyer():
    return User.objects.create_user("buyer@example.test")


@pytest.fixture
def plan():
    category = Category.objects.create(name="نمونه", slug="sample")
    product = Product.objects.create(
        category=category,
        name="سرویس نمونه",
        slug="sample",
        status=Product.Status.ACTIVE,
        warranty="هفت روز",
    )
    return Plan.objects.create(
        product=product,
        title="یک ماهه",
        sale_price=Decimal("200000"),
        cost_price=Decimal("100000"),
        duration_days=30,
        max_quantity=3,
    )

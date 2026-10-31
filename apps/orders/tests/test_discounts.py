import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Plan
from apps.catalog.tests.test_admin import form_of
from apps.orders import services
from apps.orders.models import DiscountCode, DiscountRedemption, Order, OrderItem
from apps.orders.services import OrderLine
from apps.orders.tests.test_lifecycle_concurrency import separate_plan

pytestmark = pytest.mark.django_db


def test_selected_scope_caps_fixed_discount_and_tests_minimum_before_the_code(buyer, plan):
    other = separate_plan(plan)
    Plan.objects.filter(pk=plan.pk).update(sale_price=100)
    Plan.objects.filter(pk=other.pk).update(sale_price=200)
    code = DiscountCode.objects.create(
        code="SAVE", kind="fixed", value=400, scope="selected", min_order_amount=300
    )
    code.products.add(plan.product)
    order = services.place_order(
        user=buyer,
        lines=[OrderLine(plan, 1), OrderLine(other, 1)],
        discount_code=" save ",
    )
    assert (order.subtotal, order.discount_amount, order.total_amount) == (300, 100, 200)
    code.value = Decimal(1)
    code.scope = "all"
    code.save()
    code.products.clear()
    order.refresh_from_db()
    assert order.discount_amount == 100
    assert order.discount_snapshot["scope"] == "selected"
    assert order.discount_snapshot["products"] == [plan.product_id]
    assert DiscountRedemption.objects.get(order=order).amount == 100


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"is_active": False}, "فعال نیست"),
        ({"valid_from": timezone.now() + timedelta(days=1)}, "هنوز نرسیده"),
        ({"valid_until": timezone.now() - timedelta(seconds=1)}, "تمام شده"),
        ({"max_uses": 1, "used_count": 1}, "ظرفیت این کد"),
        ({"min_order_amount": 300000}, "کافی نیست"),
        ({"scope": "selected"}, "این محصولات"),
    ],
)
def test_coupon_rejection_is_specific_and_preserves_resources(buyer, plan, changes, error):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    code = DiscountCode.objects.create(code="SAVE", kind="fixed", value=100, **changes)
    with pytest.raises(ValidationError, match=error):
        services.place_order(user=buyer, lines=[OrderLine(plan, 1)], discount_code=code.code)
    plan.refresh_from_db()
    assert plan.stock_quantity == 1
    assert not Order.objects.exists() and not DiscountRedemption.objects.exists()


def test_per_customer_limit_counts_active_history_beyond_the_unpaid_order(buyer, plan):
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=100, per_user_limit=1)
    order = services.place_order(user=buyer, lines=[OrderLine(plan, 1)], discount_code="SAVE")
    OrderItem.objects.filter(order=order).update(status=OrderItem.Status.QUEUED)
    with pytest.raises(ValidationError, match="تعداد مجاز"):
        services.place_order(user=buyer, lines=[OrderLine(plan, 1)], discount_code="SAVE")
    assert DiscountRedemption.objects.count() == 1


@pytest.mark.parametrize("code", ["MISSING", "bad!", "ab", "x" * 40])
def test_unknown_and_malformed_codes_do_not_silently_disappear(buyer, plan, code):
    with pytest.raises(ValidationError, match="این کد تخفیف درست نیست"):
        services.place_order(user=buyer, lines=[OrderLine(plan, 1)], discount_code=code)
    assert not Order.objects.exists()


def test_idempotent_replay_precedes_empty_lines_and_expiry_checks(buyer, plan):
    checkout_id = uuid.uuid4()
    order = services.place_order(
        user=buyer,
        lines=[OrderLine(plan, 1)],
        checkout_id=checkout_id,
        submission_digest="a" * 64,
    )
    assert order.consent_text == ""
    assert order.consent_version == ""
    assert order.consent_accepted_at is None
    services.cancel_unpaid_order(order_id=order.pk, actor=buyer)
    replay = services.place_order(
        user=buyer, lines=[], checkout_id=checkout_id, submission_digest="a" * 64
    )
    assert replay.pk == order.pk
    assert replay.unpaid_expires_at == order.unpaid_expires_at
    for user, digest in [
        (buyer, "b" * 64),
        (User.objects.create_user("other@example.test"), "a" * 64),
    ]:
        with pytest.raises(ValidationError, match="قبلاً با اطلاعات دیگری"):
            services.place_order(
                user=user, lines=[], checkout_id=checkout_id, submission_digest=digest
            )
    User.objects.filter(pk=buyer.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        services.place_order(
            user=buyer, lines=[], checkout_id=checkout_id, submission_digest="a" * 64
        )
    assert Order.objects.count() == 1


def test_operator_can_create_normalized_coupon_and_history_stays_read_only(client, buyer, plan):
    client.force_login(User.objects.create_superuser("operator@example.test"))
    listing = reverse("admin:orders_discountcode_changelist")
    assert listing in client.get(reverse("admin:index")).content.decode()
    url = reverse("admin:orders_discountcode_add")
    data = form_of(client, url, form_id="discountcode_form")
    data.update(code=[" save "], kind=["fixed"], value=["100"], scope=["all"], is_active=["on"])
    response = client.post(url, data)
    assert response.status_code == 302, response.context["adminform"].form.errors
    code = DiscountCode.objects.get(code="SAVE")
    order = services.place_order(user=buyer, lines=[OrderLine(plan, 1)], discount_code="SAVE")
    change = reverse("admin:orders_discountcode_change", args=[code.pk])
    data = form_of(client, change, form_id="discountcode_form")
    assert "used_count" not in data and not any(name.endswith("-amount") for name in data)
    data.update(value=["200"], used_count=["0"])
    assert client.post(change, data).status_code == 302
    code.refresh_from_db()
    assert code.used_count == 1 and code.value == 200
    assert DiscountRedemption.objects.get(order=order).amount == 100
    assert (
        client.post(reverse("admin:orders_discountcode_delete", args=[code.pk])).status_code == 403
    )


@pytest.mark.parametrize("field", ["value", "max_uses", "per_user_limit", "min_order_amount"])
def test_discount_editor_points_to_nonpositive_values(client, field):
    client.force_login(User.objects.create_superuser("operator@example.test"))
    url = reverse("admin:orders_discountcode_add")
    data = form_of(client, url, form_id="discountcode_form")
    data.update(code=["SAVE"], kind=["fixed"], value=["100"], scope=["all"], is_active=["on"])
    data[field] = ["-1" if field == "min_order_amount" else "0"]
    response = client.post(url, data)
    assert response.status_code == 200
    assert field in response.context["adminform"].form.errors
    assert not DiscountCode.objects.exists()


def test_discount_editor_requires_a_selected_product(client):
    client.force_login(User.objects.create_superuser("operator@example.test"))
    url = reverse("admin:orders_discountcode_add")
    data = form_of(client, url, form_id="discountcode_form")
    data.update(code=["SAVE"], kind=["fixed"], value=["100"], scope=["selected"], is_active=["on"])
    response = client.post(url, data)
    assert response.status_code == 200
    assert "products" in response.context["adminform"].form.errors


def test_discount_editor_rejects_an_inverted_validity_window(client):
    client.force_login(User.objects.create_superuser("operator@example.test"))
    url = reverse("admin:orders_discountcode_add")
    data = form_of(client, url, form_id="discountcode_form")
    data.update(
        code=["SAVE"],
        kind=["fixed"],
        value=["100"],
        scope=["all"],
        is_active=["on"],
        valid_from_0=["2026-10-30"],
        valid_from_1=["12:00:00"],
        valid_until_0=["2026-10-29"],
        valid_until_1=["12:00:00"],
    )
    response = client.post(url, data)
    assert response.status_code == 200
    assert "valid_until" in response.context["adminform"].form.errors
    assert "پایان اعتبار باید پس از شروع آن باشد" in response.content.decode()

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.catalog.tests.test_admin import form_of, plan_prefix
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("intentional", [False, True])
def test_stale_stock_form_preserves_checkout_or_requests_operator_review(
    client, buyer, plan, inline, intentional
):
    operator = User.objects.create_superuser("operator@example.test", "a-test-passphrase-320!")
    client.force_login(operator)
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=3)
    Product.objects.filter(pk=plan.product_id).update(
        delivery_type=Product.DeliveryType.READY_ACCOUNT
    )
    model, pk = ("product", plan.product_id) if inline else ("plan", plan.pk)
    url = reverse(f"admin:catalog_{model}_change", args=[pk])
    opened = client.get(url)
    assert opened.status_code == 200
    data = form_of(client, url, form_id="product_form" if inline else "plan_form")
    prefix = plan_prefix(data, plan) + "-" if inline else ""
    stock = prefix + "stock_quantity"
    assert data[stock] == ["3"]
    place_order(user=buyer, lines=[OrderLine(plan, 1)], activation_consent=True)
    data[stock] = ["6" if intentional else "3"]
    data[prefix + "title"] = ["اشتراک یک‌ماهه"]
    response = client.post(url, data)
    errors = (
        [response.context["adminform"].form.errors]
        + [formset.formset.errors for formset in response.context["inline_admin_formsets"]]
        if response.status_code == 200
        else []
    )
    assert response.status_code == (200 if intentional else 302), errors
    plan.refresh_from_db()
    assert plan.stock_quantity == 2
    if intentional:
        assert "موجودی تغییر کرده است" in response.content.decode()


@pytest.mark.parametrize("token", ["", "forged"])
def test_stock_editor_refuses_missing_or_forged_original_quantities(client, plan, token):
    client.force_login(User.objects.create_superuser("operator@example.test"))
    url = reverse("admin:catalog_plan_change", args=[plan.pk])
    data = form_of(client, url, form_id="plan_form")
    data["stock_version"] = [token]
    data["stock_quantity"] = ["9"]
    response = client.post(url, data)
    assert response.status_code == 200
    assert "صفحه‌ی ویرایش معتبر نیست" in response.content.decode()
    plan.refresh_from_db()
    assert plan.stock_quantity is None

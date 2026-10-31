import pytest

from apps.catalog.models import Plan

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "quantity,message",
    [(0, "باید حداقل ۱ تا انتخاب کنید."), (4, "از این گزینه حداکثر ۳ تا می‌توانید بخرید.")],
)
def test_quantity_errors_name_the_limit(client, plan, quantity, message):
    response = client.post(
        "/cart/add/", {"plan": plan.pk, "quantity": quantity}, HTTP_ACCEPT="application/json"
    )
    assert response.status_code == 400
    assert response.json()["message"] == message


def test_current_stock_error_and_cart_limit_use_short_labels(client, plan):
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=2)
    response = client.post(
        "/cart/add/", {"plan": plan.pk, "quantity": 3}, HTTP_ACCEPT="application/json"
    )
    assert response.json()["message"] == "از این گزینه فقط ۲ تا موجود است."
    client.post("/cart/add/", {"plan": plan.pk})
    body = client.get("/cart/").content.decode()
    assert "حداکثر ۲ تا می‌توانید بخرید." in body

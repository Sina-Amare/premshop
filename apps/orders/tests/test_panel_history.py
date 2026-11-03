"""The order page in the panel: its saved-conditions labels follow the panel's
language, and the customer's copy of the same partial never does."""

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.orders.selectors import grouped_items
from apps.orders.services import OrderLine, place_order

pytestmark = pytest.mark.django_db

PERSIAN_LABELS = (
    "شرایط زمان خرید",
    "روش تحویل:",
    "گارانتی:",
    "تحویل تا ۱۲ ساعت پس از تأیید پرداخت",
    "برای فعال‌سازی این محصول باید در تلگرام هماهنگ کنید.",
    "<h4>نکات استفاده</h4>",
    "<h4>شرایط لغو و بازگشت وجه</h4>",
    "<h4>توضیحات محصول</h4>",
)
ENGLISH_LABELS = (
    "Conditions at purchase",
    "Delivery method:",
    "Warranty:",
    "Delivery within ۱۲ hours of payment confirmation",
    "Activating this product needs coordination on Telegram.",
    "<h4>Usage notes</h4>",
    "<h4>Cancellation and refund terms</h4>",
    "<h4>Product description</h4>",
)


@pytest.fixture
def order(buyer, plan):
    Product.objects.filter(pk=plan.product_id).update(
        delivery_type=Product.DeliveryType.READY_ACCOUNT,
        delivery_hours=12,
        delivery_note="",
        usage_notes="یادداشت استفاده",
        refund_note="یادداشت بازگشت",
        full_description="توضیح کامل",
    )
    Plan.objects.filter(pk=plan.pk).update(telegram_required=True)
    return place_order(user=buyer, lines=[OrderLine(plan, 1)])


@pytest.fixture
def operator(client):
    user = User.objects.create_superuser("operator@example.test")
    client.force_login(user)
    return user


def panel_page(client, order, language):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
    response = client.get(reverse("admin:orders_order_change", args=[order.pk]))
    assert response.status_code == 200
    return response.content.decode()


def test_an_english_panel_shows_the_saved_conditions_in_english(client, order, operator):
    body = panel_page(client, order, "en")

    for label in ENGLISH_LABELS:
        assert label in body, label
    for label in PERSIAN_LABELS:
        assert label not in body, label


def test_a_persian_panel_keeps_the_saved_conditions_in_persian(client, order, operator):
    body = panel_page(client, order, "fa")

    for label in PERSIAN_LABELS:
        assert label in body, label
    for label in ENGLISH_LABELS:
        assert label not in body, label


def test_the_customers_copy_of_the_conditions_is_persian_whatever_language_is_active(
    client, buyer, order
):
    """The middleware already keeps the shop Persian outside the panel; this
    holds the template to the same, so it does not rest on the middleware."""
    with translation.override("en"):
        rendered = render_to_string(
            "orders/_groups.html", {"groups": grouped_items(order.items.all())}
        )
    for label in PERSIAN_LABELS:
        assert label in rendered, label
    for label in ENGLISH_LABELS:
        assert label not in rendered, label

    client.force_login(buyer)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    page = client.get(
        reverse("order-detail", args=[order.order_number]), headers={"accept-language": "en"}
    )
    assert "تحویل تا ۱۲ ساعت پس از تأیید پرداخت" in page.content.decode()

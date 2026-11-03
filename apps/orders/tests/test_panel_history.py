"""The order page in the panel: its saved-conditions labels follow the panel's
language, the customer's copy of the same partial never does, and the two
histories name what they are about."""

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import User
from apps.catalog.models import Plan, Product
from apps.core.formatting import to_persian_digits
from apps.orders.models import CredentialAccessLog, OrderItemEvent
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


def section(body, start):
    """The `<section>` that opens at `start` and runs to its closing tag."""
    return body[body.index(start) :].split("</section>", 1)[0]


def test_both_histories_name_the_product_and_plan_and_never_the_input(client, buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(
        input_type="email", requires_customer_input=True, max_quantity=1
    )
    order = place_order(user=buyer, lines=[OrderLine(plan, 1, "private-target@example.test")])
    item = order.items.get()
    operator = User.objects.create_superuser("operator@example.test")
    CredentialAccessLog.objects.create(order_item=item, user=operator)
    client.force_login(operator)

    body = panel_page(client, order, "fa")

    timeline = section(body, 'aria-labelledby="order-history-title"')
    access = section(body, 'id="input-access-history"')
    named = "<bdi>سرویس نمونه</bdi>؛ <bdi>یک ماهه</bdi>"
    assert OrderItemEvent.objects.filter(order_item=item).exists()
    assert named in timeline and named in access
    number = f"آیتم {to_persian_digits(item.pk)}"
    assert number in timeline and number in access
    assert "private-target@example.test" not in body

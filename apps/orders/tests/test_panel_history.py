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
            "orders/_saved_terms.html",
            {"groups": grouped_items(order.items.all()), "policies": {}},
        )
    # The first label heads the panel's copy; the order page names its own disclosure.
    for label in PERSIAN_LABELS[1:]:
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
    target = "private-target@example.test"
    # A decoy order first, so the item's database id is not its place in its order.
    decoy = User.objects.create_user("decoy@example.test")
    place_order(user=decoy, lines=[OrderLine(plan, 1, "decoy-input@example.test")])
    order = place_order(user=buyer, lines=[OrderLine(plan, 1, target)])
    item = order.items.get()
    operator = User.objects.create_superuser("operator@example.test")
    CredentialAccessLog.objects.create(order_item=item, user=operator, target="customer_input")
    client.force_login(operator)

    body = panel_page(client, order, "fa")

    timeline = section(body, 'aria-labelledby="order-history-title"')
    access = section(body, 'id="input-access-history"')
    named = "<bdi>سرویس نمونه</bdi>، <bdi>یک ماهه</bdi>"
    assert OrderItemEvent.objects.filter(order_item=item).exists()
    assert named in timeline and named in access
    number, database_id = "آیتم ۱،", f"آیتم {to_persian_digits(item.pk)}،"
    assert item.pk != 1, "positive control: the id differs from the place"
    assert number in timeline and number in access
    assert database_id not in timeline and database_id not in access
    # Stored, so its absence from the page is the page's doing and not a lost input.
    assert item.customer_input == target
    assert target not in body


def test_the_order_page_query_count_is_the_same_for_two_items_and_six(
    client, buyer, plan, queries_for
):
    """Both histories name each item from its saved record, so a row must not cost
    a query of its own."""
    operator = User.objects.create_superuser("operator@example.test")
    client.force_login(operator)

    def order_of(count, start, customer):
        plans = []
        for i in range(start, start + count):
            product = Product.objects.create(
                category=plan.product.category,
                name=f"محصول {i}",
                slug=f"history-{i}",
                status=Product.Status.ACTIVE,
            )
            plans.append(
                Plan.objects.create(
                    product=product,
                    title="۱ ماهه",
                    cost_price=1,
                    sale_price=200000,
                    max_quantity=3,
                    input_type=Plan.InputType.NONE,
                    telegram_required=False,
                )
            )
        order = place_order(user=customer, lines=[OrderLine(p, 1) for p in plans])
        for item in order.items.all():
            CredentialAccessLog.objects.create(
                order_item=item, user=operator, target="customer_input"
            )
            OrderItemEvent.objects.create(
                order_item=item,
                from_status=item.status,
                to_status=item.status,
                actor=OrderItemEvent.Actor.SYSTEM,
            )
        return order

    other = User.objects.create_user("other@example.test")
    small, large = order_of(2, 0, buyer), order_of(6, 2, other)

    few, response = queries_for(reverse("admin:orders_order_change", args=[small.pk]))
    assert len(response.context["input_access_log"]) == 2
    many, response = queries_for(reverse("admin:orders_order_change", args=[large.pk]))

    assert len(response.context["input_access_log"]) == 6
    assert many == few

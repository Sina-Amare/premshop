import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.forms import modelform_factory

from apps.catalog.admin import PlanForm
from apps.catalog.models import Plan
from apps.orders.models import Order
from apps.orders.services import OrderLine, place_order, quote_order

pytestmark = pytest.mark.django_db


def configure(plan, kind="email", telegram=True):
    Plan.objects.filter(pk=plan.pk).update(
        input_type=kind,
        telegram_required=telegram,
        requires_customer_input=kind in ("email", "username"),
        max_quantity=1,
    )
    plan.refresh_from_db()


def test_unreviewed_legacy_plan_cannot_be_quoted_or_purchased(buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(input_type="unreviewed", telegram_required=None)
    for operation in (quote_order, place_order):
        with pytest.raises(ValidationError, match="از پشتیبانی راهنمایی بگیرید"):
            operation(user=buyer, lines=[OrderLine(plan, 1)])
    assert not Order.objects.exists()


def test_quote_needs_no_input_but_purchase_validates_and_encrypts_it(buyer, plan):
    configure(plan)
    terms = quote_order(user=buyer, lines=[OrderLine(plan, 1)]).terms()
    with pytest.raises(ValidationError, match="ایمیل"):
        place_order(user=buyer, lines=[OrderLine(plan, 1)])
    item = place_order(
        user=buyer,
        lines=[OrderLine(plan, 1, " Target@EXAMPLE.test ")],
        expected_terms=terms,
    ).items.get()
    assert item.customer_input == "Target@example.test"
    assert item.product_snapshot["input_type"] == "email"
    assert item.product_snapshot["telegram_required"] is True
    assert item.product_snapshot["customer_input_label"] == "ایمیل اکانت"
    with connection.cursor() as cursor:
        cursor.execute("SELECT customer_input FROM orders_orderitem WHERE id=%s", [item.pk])
        assert "Target" not in cursor.fetchone()[0]


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("email", "not-email"),
        ("email", "a@b.test\x00"),
        ("username", "a b"),
        ("username", "https://example.test/user"),
        ("username", "a@b.test"),
        ("none", "unrequested"),
        ("username", "a" * 151),
        ("email", {"a": "b"}),
    ],
)
def test_invalid_or_unsolicited_input_preserves_stock_and_history(buyer, plan, kind, value):
    configure(plan, kind)
    Plan.objects.filter(pk=plan.pk).update(stock_quantity=1)
    with pytest.raises(ValidationError):
        place_order(user=buyer, lines=[OrderLine(plan, 1, value)])
    plan.refresh_from_db()
    assert plan.stock_quantity == 1 and not Order.objects.exists()


def test_username_is_normalized_and_requirement_changes_need_new_confirmation(buyer, plan):
    configure(plan, "username", False)
    terms = quote_order(user=buyer, lines=[OrderLine(plan, 1)]).terms()
    configure(plan, "email", False)
    with pytest.raises(ValidationError) as error:
        place_order(
            user=buyer,
            lines=[OrderLine(plan, 1, "@target.name")],
            expected_terms=terms,
        )
    assert error.value.code == "quote_changed"
    configure(plan, "username", False)
    item = place_order(user=buyer, lines=[OrderLine(plan, 1, " @target.name ")]).items.get()
    assert item.customer_input == "target.name"


@pytest.mark.parametrize(
    "changes",
    [
        {"input_type": "password"},
        {"input_type": "email", "telegram_required": None},
        {"input_type": "email", "telegram_required": False, "requires_customer_input": False},
    ],
)
def test_database_rejects_inconsistent_reviewed_declarations(plan, changes):
    with pytest.raises(IntegrityError), transaction.atomic():
        Plan.objects.filter(pk=plan.pk).update(**changes)


def test_admin_requires_review_and_maps_quantity_error_to_visible_field(plan):
    form_class = modelform_factory(
        Plan,
        form=PlanForm,
        fields=[
            "input_type",
            "telegram_required",
            "max_quantity",
            "customer_input_label",
        ],
    )
    form = form_class(instance=plan, data={"input_type": "email", "max_quantity": 2})
    assert not form.is_valid()
    assert "telegram_required" in form.errors and "max_quantity" in form.errors
    form = form_class(
        instance=plan,
        data={
            "input_type": "username",
            "telegram_required": "False",
            "max_quantity": 1,
        },
    )
    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.requires_customer_input is True and saved.telegram_required is False


def test_reviewed_no_input_does_not_retain_a_stale_secret_label(buyer, plan):
    configure(plan, "none", False)
    Plan.objects.filter(pk=plan.pk).update(customer_input_label="رمز عبور اکانت")
    with pytest.raises(ValidationError, match="از پشتیبانی راهنمایی بگیرید"):
        quote_order(user=buyer, lines=[OrderLine(plan, 1)])


def test_activation_input_is_not_in_line_debug_representation(plan):
    assert "private-target" not in repr(OrderLine(plan, 1, "private-target@example.test"))


def test_reviewed_no_input_hides_legacy_hint_without_erasing_it(client, buyer, plan):
    Plan.objects.filter(pk=plan.pk).update(customer_input_label="ایمیل اکانت قبلی")
    plan.refresh_from_db()
    assert plan.activation_input_label == ""
    assert plan.customer_input_label == "ایمیل اکانت قبلی"
    response = client.get(plan.product.get_absolute_url())
    assert response.status_code == 200
    assert 'data-asks=""' in response.content.decode()
    assert "ایمیل اکانت قبلی" not in response.content.decode()
    purchase = quote_order(user=buyer, lines=[OrderLine(plan, 1)])
    assert purchase.lines[0].snapshot["customer_input_label"] == ""


@pytest.mark.django_db(transaction=True)
def test_requirement_migration_preserves_legacy_values_without_guessing_configuration(plan):
    configure(plan, "email")
    Plan.objects.filter(pk=plan.pk).update(customer_input_label="ایمیل اکانت مقصد")
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([("catalog", "0029_available_stock_help")])
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT requires_customer_input, customer_input_label, sale_price FROM catalog_plan WHERE id=%s",
                [plan.pk],
            )
            assert cursor.fetchone() == (True, "ایمیل اکانت مقصد", 200000)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        plan.refresh_from_db()
        assert plan.input_type == "unreviewed" and plan.telegram_required is None
        assert (
            plan.requires_customer_input is True and plan.customer_input_label == "ایمیل اکانت مقصد"
        )
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

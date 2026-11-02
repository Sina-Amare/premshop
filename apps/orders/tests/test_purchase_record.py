import hashlib
import uuid
from copy import deepcopy

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import strip_tags

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product, ProductQuestion, ProductSection
from apps.cms.models import Page
from apps.orders.models import DiscountRedemption, Order, OrderItemEvent
from apps.orders.services import OrderLine, place_order, quote_order
from apps.payments.services import checkout, quote_checkout

pytestmark = pytest.mark.django_db


@pytest.fixture
def offer(plan):
    Product.objects.filter(pk=plan.product_id).update(
        full_description="توضیح کامل زمان خرید **Example Pro**",
        refund_note="بازگشت وجه زمان خرید",
        usage_notes="نکات استفاده زمان خرید",
    )
    for title, body, position in (
        ("بخش دوم", "شرایط جایگزینی زمان خرید", 2),
        ("بخش اول", "شرایط استفاده زمان خرید", 1),
    ):
        ProductSection.objects.create(
            product=plan.product, kind="free", title=title, body=body, sort_order=position
        )
    ProductQuestion.objects.create(
        product=plan.product, question="پرسش نمونه", answer="پاسخ خارج از قرارداد"
    )
    return plan


@pytest.fixture
def policies():
    result = {}
    for slug, title in (("terms", "قوانین خرید"), ("refunds", "بازگشت وجه"), ("contact", "تماس")):
        result[slug], _ = Page.objects.update_or_create(
            slug=slug,
            defaults={
                "title": title,
                "body": f"متن ثبت‌شده {slug}",
                "description": "توضیح صفحه",
                "is_published": True,
            },
        )
    return result


@pytest.fixture
def cart(buyer, offer):
    cart = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=cart, plan=offer, quantity=1)
    return cart


def test_record_keeps_ordered_product_prose_and_published_pages(buyer, offer, policies):
    terms = quote_order(user=buyer, lines=[OrderLine(offer, 1)]).terms()
    assert terms["policies"]["contact"]["body"] == "متن ثبت‌شده contact"
    order = place_order(user=buyer, lines=[OrderLine(offer, 1)])
    facts = order.items.get().product_snapshot
    assert facts["full_description"] == "توضیح کامل زمان خرید **Example Pro**"
    assert facts["sections"] == [
        {"kind": "free", "title": "بخش اول", "body": "شرایط استفاده زمان خرید"},
        {"kind": "free", "title": "بخش دوم", "body": "شرایط جایگزینی زمان خرید"},
    ]
    assert "questions" not in facts
    assert set(order.policy_snapshot) == {"terms", "refunds", "contact"}
    expected = '{"body":"متن ثبت‌شده terms","title":"قوانین خرید"}'
    assert order.policy_snapshot["terms"] == {
        "title": "قوانین خرید",
        "body": "متن ثبت‌شده terms",
        "content_hash": hashlib.sha256(expected.encode()).hexdigest(),
    }
    saved = deepcopy(order.policy_snapshot)
    Product.objects.filter(pk=offer.product_id).update(full_description="توضیح جدید")
    offer.product.sections.all().delete()
    Page.objects.filter(slug__in=policies).update(body="متن جدید", is_published=False)
    order.refresh_from_db()
    assert order.policy_snapshot == saved
    assert order.items.get().product_snapshot == facts


@pytest.mark.parametrize("mutation", ["body", "title", "unpublish", "delete", "publish"])
@pytest.mark.parametrize("slug", ["terms", "refunds", "contact"])
def test_policy_changes_require_review_without_consuming_checkout(
    buyer, offer, cart, policies, mutation, slug
):
    page = policies[slug]
    if mutation == "publish":
        Page.objects.filter(pk=page.pk).update(is_published=False)
    quote = quote_checkout(user=buyer)
    if mutation == "delete":
        page.delete()
    else:
        updates = {
            "body": {"body": "متن تغییر کرده"},
            "title": {"title": "عنوان تغییر کرده"},
            "unpublish": {"is_published": False},
            "publish": {"is_published": True},
        }
        Page.objects.filter(pk=page.pk).update(**updates[mutation])
    with pytest.raises(ValidationError) as error:
        checkout(user=buyer, quote=quote.token, checkout_id=uuid.uuid4(), customer_inputs={})
    assert error.value.code == "quote_changed"
    assert not Order.objects.exists() and not OrderItemEvent.objects.exists()
    assert not DiscountRedemption.objects.exists() and cart.items.count() == 1


@pytest.mark.parametrize("mutation", ["description", "section", "title", "order", "delete", "add"])
def test_product_prose_changes_require_review(buyer, offer, mutation):
    expected = quote_order(user=buyer, lines=[OrderLine(offer, 1)]).terms_digest()
    section = offer.product.sections.first()
    if mutation == "description":
        Product.objects.filter(pk=offer.product_id).update(full_description="توضیح جدید")
    elif mutation == "delete":
        section.delete()
    elif mutation == "add":
        ProductSection.objects.create(product=offer.product, kind="free", body="بخش تازه")
    else:
        updates = {
            "section": {"body": "شرایط تازه"},
            "title": {"title": "عنوان تازه"},
            "order": {"sort_order": 9},
        }
        ProductSection.objects.filter(pk=section.pk).update(**updates[mutation])
    with pytest.raises(ValidationError) as error:
        place_order(user=buyer, lines=[OrderLine(offer, 1)], expected_terms=expected)
    assert error.value.code == "quote_changed"
    assert not Order.objects.exists()


def test_drafts_and_questions_are_excluded_without_blocking_placement(buyer, offer, policies):
    Page.objects.filter(slug__in=policies).update(is_published=False)
    expected = quote_order(user=buyer, lines=[OrderLine(offer, 1)]).terms()
    Page.objects.filter(slug__in=policies).update(body="مسوده خصوصی جدید")
    offer.product.questions.all().update(answer="پاسخ جدید")
    order = place_order(user=buyer, lines=[OrderLine(offer, 1)], expected_terms=expected)
    assert order.policy_snapshot == {}
    assert expected["policies"] == {}


def test_only_the_three_purchase_pages_affect_review(buyer, offer, policies):
    page, _ = Page.objects.update_or_create(
        slug="privacy",
        defaults={
            "title": "حریم خصوصی",
            "body": "متن حریم خصوصی",
            "description": "توضیح حریم خصوصی",
            "is_published": True,
        },
    )
    expected = quote_order(user=buyer, lines=[OrderLine(offer, 1)]).terms_digest()
    Page.objects.filter(pk=page.pk).update(body="متن تازه حریم خصوصی")
    order = place_order(user=buyer, lines=[OrderLine(offer, 1)], expected_terms=expected)
    assert set(order.policy_snapshot) == {"terms", "refunds", "contact"}


def test_replay_preserves_record_after_content_and_publication_changes(buyer, cart, policies):
    quote = quote_checkout(user=buyer)
    key = uuid.uuid4()
    order = checkout(user=buyer, quote=quote.token, checkout_id=key, customer_inputs={})
    saved = deepcopy(order.policy_snapshot)
    Page.objects.filter(slug__in=policies).delete()
    Product.objects.all().update(full_description="توضيح جديد")
    replay = checkout(user=buyer, quote=quote.token, checkout_id=key, customer_inputs={})
    assert replay.pk == order.pk and replay.policy_snapshot == saved
    assert replay.items.get().product_snapshot == order.items.get().product_snapshot
    assert Order.objects.count() == 1


def test_saved_conditions_are_owner_only_and_panel_can_inspect_another_customer(
    client, buyer, offer, policies, settings
):
    order = place_order(user=buyer, lines=[OrderLine(offer, 1)])
    Page.objects.filter(slug__in=policies).update(body="نص جديد لا يظهر")
    Product.objects.filter(pk=offer.product_id).update(full_description="نص حالي لا يظهر")
    operator = User.objects.create_superuser("operator@example.test")
    assert order.user_id != operator.pk
    detail = reverse("order-detail", args=[order.order_number])
    panel = reverse("admin:orders_order_change", args=[order.pk])
    for account, url in ((buyer, detail), (operator, panel)):
        client.force_login(account)
        settings.FIELD_ENCRYPTION_KEY = ""
        response = client.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        for saved in (
            "توضیح کامل زمان خرید",
            "شرایط جایگزینی زمان خرید",
            "بازگشت وجه زمان خرید",
            "نکات استفاده زمان خرید",
            "متن ثبت‌شده terms",
            "متن ثبت‌شده refunds",
            "متن ثبت‌شده contact",
        ):
            assert saved in strip_tags(body)
        assert "نص جديد لا يظهر" not in body and "نص حالي لا يظهر" not in body
        assert "پاسخ خارج از قرارداد" not in body
        assert 'name="policy_snapshot"' not in body
        assert "no-store" in response["Cache-Control"]
    assert client.get(detail).status_code == 404
    assert client.post(panel, {"policy_snapshot": "{}"}).status_code == 403
    client.force_login(User.objects.create_user("staff@example.test", is_staff=True))
    assert client.get(panel).status_code == 403
    tracking = client.get(reverse("order-tracking", args=[order.tracking_token])).content.decode()
    assert "متن ثبت‌شده" not in tracking and "شرایط جایگزینی زمان خرید" not in tracking


def test_old_snapshots_and_empty_policy_record_render_without_backfill(client, buyer, offer):
    order = place_order(user=buyer, lines=[OrderLine(offer, 1)])
    order.items.update(product_snapshot={"name": "محصول قبلی", "plan_title": "پلن قبلی"})
    Order.objects.filter(pk=order.pk).update(policy_snapshot={})
    for account, url in (
        (buyer, reverse("order-detail", args=[order.order_number])),
        (
            User.objects.create_superuser("operator@example.test"),
            reverse("admin:orders_order_change", args=[order.pk]),
        ),
    ):
        client.force_login(account)
        response = client.get(url)
        assert response.status_code == 200
        assert "محصول قبلی" in response.content.decode()
        assert "توضیح کامل زمان خرید" not in response.content.decode()

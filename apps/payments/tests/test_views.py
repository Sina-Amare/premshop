import uuid

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Plan, Product
from apps.orders.models import DiscountCode, Order
from apps.payments.services import quote_checkout

pytestmark = pytest.mark.django_db


@pytest.fixture
def cart(buyer, plan):
    cart = Cart.objects.create(user=buyer)
    CartItem.objects.create(cart=cart, plan=plan)
    return cart


def test_guest_checkout_has_inline_code_and_html_fallback(client):
    response = client.get(reverse("checkout"))
    assert response.status_code == 200
    body = response.content.decode()
    assert reverse("login-code") in body and reverse("login-code-verify") in body
    assert "برای ثبت سفارش وارد شوید" in body
    assert 'name="next" value="/checkout/"' in body
    assert "no-store" in response.headers["Cache-Control"]
    assert client.post(reverse("checkout"), {"action": "checkout"}).status_code == 302


@pytest.mark.parametrize(
    ("code", "label"),
    [
        ("none", "ندارد"),
        ("days_7", "۷ روز"),
        ("full_period", "تا پایان دوره"),
    ],
)
def test_checkout_shows_persian_warranty_labels_and_preserves_snapshot_code(
    client, buyer, plan, cart, code, label
):
    Product.objects.filter(pk=plan.product_id).update(warranty=code)
    client.force_login(buyer)
    response = client.get(reverse("checkout"))
    body = response.content.decode()
    assert f"گارانتی: {label}" in body
    assert f"ضمانت: {code}" not in body
    assert response.context["quote"].purchase.lines[0].snapshot["warranty"] == code


def test_inline_code_request_preserves_safe_next_and_returns_json(client, mailoutbox):
    response = client.post(
        reverse("login-code"),
        {
            "email": "buyer@example.test",
            "next": "/checkout/",
        },
        HTTP_ACCEPT="application/json",
    )
    assert response.status_code == 200 and response.json()["ok"] is True
    assert response.json()["expires_in"] > 0
    assert client.session["accounts.pending_next"] == reverse("checkout")
    assert len(mailoutbox) == 1
    bad = client.post(reverse("login-code"), {"email": "wrong"}, HTTP_ACCEPT="application/json")
    assert bad.status_code == 400 and bad.json()["ok"] is False
    verify = client.post(
        reverse("login-code-verify"), {"code": "000000"}, HTTP_ACCEPT="application/json"
    )
    assert verify.json()["ok"] is False


def test_review_and_confirmation_use_server_totals_and_unticked_consent(client, buyer, plan, cart):
    client.force_login(buyer)
    response = client.get(reverse("checkout"))
    assert response.status_code == 200
    assert "۲۰۰,۰۰۰" in response.content.decode()
    assert not response.context["form"]["activation_consent"].value()
    quote = response.context["quote"]
    response = client.post(
        reverse("checkout"),
        {
            "action": "checkout",
            "quote": quote.token,
            "checkout_id": uuid.uuid4(),
            "activation_consent": "on",
            "total_amount": "1",
        },
    )
    order = Order.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("order-detail", args=[order.order_number])
    assert order.total_amount == 200000 and not cart.items.exists()
    detail = client.get(response.url)
    assert detail.status_code == 200 and "منتظر پرداخت" in detail.content.decode()
    client.force_login(User.objects.create_user("outsider@example.test"))
    assert client.get(response.url).status_code == 404


def test_stale_summary_requires_new_explicit_submission(client, buyer, plan, cart):
    client.force_login(buyer)
    accepted = quote_checkout(user=buyer)
    Plan.objects.filter(pk=plan.pk).update(sale_price=250000)
    response = client.post(
        reverse("checkout"),
        {
            "action": "checkout",
            "quote": accepted.token,
            "checkout_id": uuid.uuid4(),
            "activation_consent": "on",
        },
    )
    assert response.status_code == 409
    assert "۲۵۰,۰۰۰" in response.content.decode()
    assert not response.context["form"]["activation_consent"].value()
    assert response.context["quote"].token != accepted.token
    assert cart.items.exists() and not Order.objects.exists()


def test_invalid_discount_is_visible_and_does_not_enable_undiscounted_purchase(client, buyer, cart):
    client.force_login(buyer)
    response = client.post(reverse("checkout"), {"action": "review", "discount_code": "WRONG"})
    assert response.status_code == 400
    assert "کد تخفیف معتبر نیست" in response.content.decode()
    assert response.context["quote"] is None
    assert 'name="action" value="checkout"' not in response.content.decode()
    assert cart.items.exists() and not Order.objects.exists()


def test_consent_and_input_errors_preserve_cart_without_echoing_input_in_errors(
    client, buyer, plan, cart
):
    Plan.objects.filter(pk=plan.pk).update(
        input_type="email", requires_customer_input=True, max_quantity=1
    )
    client.force_login(buyer)
    accepted = quote_checkout(user=buyer)
    response = client.post(
        reverse("checkout"),
        {
            "action": "checkout",
            "quote": accepted.token,
            "checkout_id": uuid.uuid4(),
            "activation_consent": "on",
            f"customer_input_{plan.pk}": "invalid-private-input",
        },
    )
    assert response.status_code == 400
    assert "ایمیل اکانت را درست وارد کنید" in response.content.decode()
    assert "invalid-private-input" not in str(response.context["error"])
    assert cart.items.exists() and not Order.objects.exists()


def test_input_errors_identify_only_the_invalid_product_and_phone(client, buyer, plan, cart):
    Plan.objects.filter(pk=plan.pk).update(
        input_type="email", requires_customer_input=True, max_quantity=1
    )
    other = Plan.objects.create(
        product=plan.product,
        title="پلن دوم",
        sale_price=100000,
        cost_price=50000,
        input_type="email",
        telegram_required=False,
    )
    CartItem.objects.create(cart=cart, plan=other)
    client.force_login(buyer)
    accepted = quote_checkout(user=buyer)
    response = client.post(
        reverse("checkout"),
        {
            "action": "checkout",
            "quote": accepted.token,
            "checkout_id": uuid.uuid4(),
            "activation_consent": "on",
            "phone": "0912",
            f"customer_input_{plan.pk}": "valid@example.test",
            f"customer_input_{other.pk}": "invalid-private-input",
        },
    )
    assert response.status_code == 400
    form = response.context["form"]
    valid = form[f"customer_input_{plan.pk}"]
    invalid = form[f"customer_input_{other.pk}"]
    assert not valid.errors and 'aria-invalid="true"' not in str(valid)
    assert invalid.errors and 'aria-invalid="true"' in str(invalid)
    assert f"{invalid.auto_id}_error" in str(invalid)
    assert form["phone"].errors and 'aria-invalid="true"' in str(form["phone"])
    assert valid.value() == "valid@example.test" and not form["activation_consent"].value()
    assert "invalid-private-input" not in str(invalid.errors)
    assert not Order.objects.exists() and cart.items.count() == 2


def test_discount_actions_say_apply_or_remove_and_show_the_result(client, buyer, cart):
    DiscountCode.objects.create(code="SAVE", kind="fixed", value=10000)
    client.force_login(buyer)
    body = client.get(reverse("checkout")).content.decode()
    assert "اعمال کد تخفیف" in body
    assert "به‌روزرسانی خلاصه" not in body
    response = client.post(
        reverse("checkout"), {"action": "apply_discount", "discount_code": "SAVE"}
    )
    assert response.status_code == 200 and response.context["quote"].purchase.total == 190000
    assert "حذف کد تخفیف" in response.content.decode()
    response = client.post(
        reverse("checkout"), {"action": "remove_discount", "discount_code": "SAVE"}
    )
    assert response.status_code == 200 and response.context["quote"].purchase.total == 200000
    assert response.context["form"]["discount_code"].value() == ""
    assert not response.context["form"]["activation_consent"].value()
    assert cart.items.exists() and not Order.objects.exists()


def test_correct_code_for_deactivated_account_preserves_guest_cart_and_does_not_sign_in(
    client, buyer, plan, cart, monkeypatch, mailoutbox
):
    monkeypatch.setattr("apps.accounts.otp.secrets.randbelow", lambda limit: 123456)
    client.post(reverse("cart-add"), {"plan": plan.pk})
    guest = Cart.objects.get(user__isnull=True)
    response = client.post(
        reverse("login-code"),
        {
            "email": buyer.email,
            "next": reverse("checkout"),
        },
        HTTP_ACCEPT="application/json",
    )
    assert response.json()["ok"] is True
    User.objects.filter(pk=buyer.pk).update(is_active=False)
    for _ in range(2):
        refused = client.post(
            reverse("login-code-verify"), {"code": "123456"}, HTTP_ACCEPT="application/json"
        )
        assert refused.json()["ok"] is False
        assert "_auth_user_id" not in client.session
    assert Cart.objects.filter(pk=guest.pk, user__isnull=True).exists()
    assert guest.items.get().quantity == cart.items.get().quantity == 1
    assert User.objects.count() == 1 and len(mailoutbox) == 1


def test_invalid_discount_keeps_activation_fields_visible_without_an_accepted_quote(
    client, buyer, plan, cart
):
    Plan.objects.filter(pk=plan.pk).update(
        input_type="email", requires_customer_input=True, max_quantity=1
    )
    client.force_login(buyer)
    name = f"customer_input_{plan.pk}"
    response = client.post(
        reverse("checkout"),
        {
            "action": "apply_discount",
            "discount_code": "WRONG",
            name: "target@example.test",
        },
    )
    assert response.status_code == 400 and response.context["quote"] is None
    assert response.context["form"][name].value() == "target@example.test"
    assert 'name="action" value="checkout"' not in response.content.decode()
    assert cart.items.exists() and not Order.objects.exists()


def test_checkout_prints_custom_delivery_once_and_hides_inapplicable_notes(
    client, buyer, plan, cart
):
    note = "تحویل پس از هماهنگی با مشتری"
    Product.objects.filter(pk=plan.product_id).update(
        delivery_note=note, usage_notes="موضوعیت ندارد."
    )
    client.force_login(buyer)
    response = client.get(reverse("checkout"))
    body = response.content.decode()
    assert body.count(note) == 1
    assert "موضوعیت ندارد" not in body and "<summary>نکات استفاده</summary>" not in body
    snapshot = response.context["quote"].purchase.lines[0].snapshot
    assert snapshot["delivery_note"] == note and snapshot["usage_notes"] == "موضوعیت ندارد."


def test_unpaid_order_message_explains_the_block_without_a_false_action(client, buyer, plan, cart):
    from apps.orders.services import OrderLine, UnpaidOrderExists, place_order

    order = place_order(user=buyer, lines=[OrderLine(plan, 1)], activation_consent=True)
    client.force_login(buyer)
    response = client.get(reverse("checkout"))
    explanation = "تا زمانی که سفارش فعلی منتظر پرداخت است، نمی‌توانید سفارش جدیدی ثبت کنید."
    assert explanation in response.content.decode()
    assert reverse("order-detail", args=[order.order_number]) in response.content.decode()
    with pytest.raises(UnpaidOrderExists) as caught:
        quote_checkout(user=buyer)
    assert explanation in caught.value.messages[0]

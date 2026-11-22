from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Barrier, Event

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.tests.test_admin import form_of
from apps.orders.models import OrderItem
from apps.orders.services import OrderLine, place_order
from apps.orders.tests.test_customer_delivery import delivered
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, separate_plan, wait_for_lock

pytestmark = pytest.mark.django_db


def reviews():
    return apps.get_model("orders", "Review")


def review_path(order, item):
    return f"/account/orders/{order.order_number}/items/{item.pk}/review/"


def test_owner_reviews_a_previously_delivered_item_once(client, buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    OrderItem.objects.filter(pk=item.pk).update(status=OrderItem.Status.REPLACEMENT_REQUESTED)
    detail = reverse("order-detail", args=[order.order_number])
    path = review_path(order, item)
    client.force_login(buyer)
    assert path in client.get(detail).content.decode()
    assert client.get(path).status_code == 405
    assert client.post(path, {"rating": 4}).status_code == 302
    assert client.post(path, {"rating": 1}).status_code == 409
    review = reviews().objects.get(order_item=item)
    assert (review.rating, review.text, review.status) == (4, "", "pending")
    pending = client.get(detail)
    assert "اگر نظرتان تأیید شود، در صفحه‌ی محصول دیده می‌شود." in pending.content.decode()
    assert "no-store" in pending.headers["Cache-Control"]
    assert path not in pending.content.decode()
    assert (
        "هنوز نظری منتشر نشده است." in client.get(plan.product.get_absolute_url()).content.decode()
    )


def test_submission_rejects_wrong_owner_order_undelivered_and_missing_csrf(
    client, buyer, plan, operator
):
    order, item = delivered(buyer, plan, operator)
    unpaid = place_order(user=buyer, lines=[OrderLine(plan, 1)])
    stranger = User.objects.create_user(phone="09123456780")
    client.force_login(stranger)
    assert client.post(review_path(order, item), {"rating": 5}).status_code == 404
    client.force_login(buyer)
    assert client.post(review_path(unpaid, item), {"rating": 5}).status_code == 404
    assert client.post(review_path(unpaid, unpaid.items.get()), {"rating": 5}).status_code == 404
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(buyer)
    assert csrf.post(review_path(order, item), {"rating": 5}).status_code == 403
    assert not reviews().objects.exists()


@pytest.mark.parametrize("rating", ["", 0, 6, "4.5"])
def test_rejects_invalid_ratings(client, buyer, plan, operator, rating):
    order, item = delivered(buyer, plan, operator)
    client.force_login(buyer)
    answer = client.post(review_path(order, item), {"rating": rating})
    assert answer.status_code == 400
    assert "امتیازی از ۱ تا ۵ انتخاب کنید." in answer.content.decode()
    assert not reviews().objects.exists()


@pytest.mark.parametrize(
    "text",
    [
        "buyer@example.test",
        "buyer＠example.test",
        "۰۹۱۲ ۳۴۵-۶۷۸۹",
        "٠٩١٢،٣٤٥،٦٧٨٩",
        "+98 (912) 345 6789",
        "۰۹۱۲‌۳۴۵‌۶۷۸۹",
        "ا" * 1001,
    ],
    ids=["email", "fullwidth", "persian", "arabic", "country-code", "joiners", "too-long"],
)
def test_contact_and_overlong_input_are_never_stored_or_shown_publicly(
    client, buyer, plan, operator, text
):
    buyer.phone = "09123456789"
    buyer.save(update_fields=["phone"])
    order, item = delivered(buyer, plan, operator)
    client.force_login(buyer)
    answer = client.post(review_path(order, item), {"rating": 1, "review_text": text})
    assert answer.status_code == 400
    # Only back to its author, in the box they edit it in, so a long review is not lost.
    page = answer.content.decode()
    assert page.count(text) == 1 and f">{text}</textarea>" in page
    assert text not in client.get(plan.product.get_absolute_url()).content.decode()
    assert not reviews().objects.exists()


@pytest.mark.parametrize(
    "text",
    ["با ۱٬۲۹۰٬۰۰۰ تومان ارزشش را داشت.", "از ۱۴۰۴/۰۸/۳۰ بدون مشکل کار می‌کند."],
    ids=["price", "date"],
)
def test_prices_and_dates_are_not_taken_for_a_phone_number(client, buyer, plan, operator, text):
    order, item = delivered(buyer, plan, operator)
    client.force_login(buyer)
    assert (
        client.post(review_path(order, item), {"rating": 5, "review_text": text}).status_code == 302
    )
    assert reviews().objects.get(order_item=item).text == text


@pytest.mark.django_db(transaction=True)
def test_two_posts_create_one_review(buyer, plan, operator):
    order, item = delivered(buyer, plan, operator)
    barrier = Barrier(2)

    def submit():
        try:
            client = Client()
            client.force_login(buyer)
            barrier.wait(timeout=10)
            return client.post(review_path(order, item), {"rating": 3}).status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(submit) for _ in range(2)]
        assert sorted(request.result(timeout=20) for request in requests) == [302, 409]
    assert reviews().objects.filter(order_item=item).count() == 1


def test_public_reviews_use_only_published_rows_and_escape_plain_text(
    client, buyer, plan, operator
):
    order, item = delivered(buyer, plan, operator)
    url = plan.product.get_absolute_url()
    assert "هنوز نظری منتشر نشده است." in client.get(url).content.decode()
    review = reviews().objects.create(
        order_item=item, rating=4, status="published", text="<script>window.review = 1</script>"
    )
    first = client.get(url).content.decode()
    assert "۴ از ۵ · ۱ نظر" in first
    assert "خریدار" in first
    assert "&lt;script&gt;window.review = 1&lt;/script&gt;" in first
    assert "<script>window.review" not in first
    for rating, state, text in [
        (5, "published", ""),
        (4, "published", ""),
        (1, "pending", "private"),
    ]:
        _, another = delivered(buyer, plan, operator)
        reviews().objects.create(order_item=another, rating=rating, status=state, text=text)
    other = separate_plan(plan)
    _, other_item = delivered(buyer, other, operator)
    reviews().objects.create(order_item=other_item, rating=1, status="published", text="other text")
    combined = client.get(url).content.decode()
    assert "۴٫۳ از ۵ · ۳ نظر" in combined
    assert "private</" not in combined and "other text" not in combined
    assert buyer.email not in combined
    review.status = "hidden"
    review.save(update_fields=["status"])
    assert "۴٫۵ از ۵ · ۲ نظر" in client.get(url).content.decode()
    client.force_login(buyer)
    detail = client.get(reverse("order-detail", args=[order.order_number])).content.decode()
    assert "نظرتان در صفحه‌ی محصول نمایش داده نمی‌شود." in detail
    assert "window.review" not in detail
    reviews().objects.filter(status="published", order_item__plan=plan).update(status="hidden")
    assert "هنوز نظری منتشر نشده است." in client.get(url).content.decode()


def test_database_rejects_bad_ratings_and_a_second_review(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    review = reviews()
    for rating in (0, 6):
        with pytest.raises(IntegrityError), transaction.atomic():
            review.objects.create(order_item=item, rating=rating)
    review.objects.create(order_item=item, rating=5)
    with pytest.raises(IntegrityError), transaction.atomic():
        review.objects.create(order_item=item, rating=5)


@pytest.mark.django_db(transaction=True)
def test_review_table_migration_reverses_with_a_review(buyer, plan, operator):
    _, item = delivered(buyer, plan, operator)
    reviews().objects.create(order_item=item, rating=5)
    executor = MigrationExecutor(connection)
    nodes = executor.loader.graph.leaf_nodes()
    previous = next(
        name
        for name in executor.loader.graph.nodes
        if name[0] == "orders" and name[1].startswith("0024_")
    )
    try:
        executor.migrate([previous])
        assert "orders_review" not in connection.introspection.table_names()
        item.refresh_from_db()
        assert item.delivered_at
    finally:
        MigrationExecutor(connection).migrate(nodes)
    assert reviews().objects.count() == 0


def test_used_plan_cannot_move_in_model_or_panel(client, buyer, plan):
    other = separate_plan(plan)
    url = reverse("admin:catalog_plan_change", args=[plan.pk])
    client.force_login(User.objects.create_superuser("staff@example.test"))
    data = form_of(client, url, "plan_form")
    assert client.post(url, {**data, "product": other.product_id}).status_code == 302
    plan.refresh_from_db()
    assert plan.product_id == other.product_id
    place_order(user=buyer, lines=[OrderLine(plan, 1)])
    plan.product_id = other.product_id
    plan.full_clean()
    origin = other.product.category.products.exclude(pk=other.product_id).get()
    plan.product = origin
    with pytest.raises(ValidationError) as raised:
        plan.full_clean()
    assert "product" in raised.value.message_dict
    response = client.post(url, {**form_of(client, url, "plan_form"), "product": origin.pk})
    assert response.status_code == 200
    assert "product" in response.context["adminform"].form.errors
    plan.refresh_from_db()
    assert plan.product_id == other.product_id


@pytest.mark.django_db(transaction=True)
def test_plan_move_waits_for_the_first_purchase(client, buyer, plan):
    other = separate_plan(plan)
    staff = User.objects.create_superuser("staff@example.test")
    client.force_login(staff)
    url = reverse("admin:catalog_plan_change", args=[plan.pk])
    data = {**form_of(client, url, "plan_form"), "product": other.product_id}
    purchased, release = Event(), Event()
    pids = Queue()

    def purchase():
        with transaction.atomic():
            place_order(user=buyer, lines=[OrderLine(plan, 1)])
            purchased.set()
            assert release.wait(10)

    def change_product():
        panel = Client()
        panel.force_login(staff)
        return panel.post(url, data).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, pids, purchase)
        holder = pids.get(timeout=10)
        assert purchased.wait(10)
        second = pool.submit(in_connection, pids, change_product)
        waiter = pids.get(timeout=10)
        try:
            wait_for_lock(waiter, holder)
        finally:
            release.set()
        first.result(timeout=15)
        assert second.result(timeout=15) == 200
    plan.refresh_from_db()
    assert plan.product_id != other.product_id

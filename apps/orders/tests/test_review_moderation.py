from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Review
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_lifecycle_concurrency import in_connection, wait_for_lock
from apps.orders.tests.test_queue_admin import make_order

pytestmark = pytest.mark.django_db
PENDING = Review.Status.PENDING
PUBLISHED = Review.Status.PUBLISHED
HIDDEN = Review.Status.HIDDEN
HIDE = {"decision": "hide", "expected_status": PENDING, "reason": "abuse_spam"}
PUBLISH = {"decision": "publish", "expected_status": PENDING}
HOME_LABEL = "نظرهای در انتظار بررسی"


def review_for(buyer, plan, status=PENDING, text="برای تماشای فیلم خوب بود.", rating=1):
    order = make_order(buyer, plan, "DELIVERED", delivered_at=timezone.now())
    return Review.objects.create(
        order_item=order.items.get(), rating=rating, text=text, status=status
    )


def panel_url(view="changelist", review=None):
    return reverse(f"admin:orders_review_{view}", args=[review.pk] if review else [])


def audit(review):
    return LogEntry.objects.filter(content_type__model="review", object_id=str(review.pk))


@pytest.mark.parametrize("permissions", [(), ("change_review",), ("view_review",)])
def test_review_permission_covers_sidebar_home_detail_history_and_post(
    client, buyer, plan, permissions
):
    review = review_for(buyer, plan, text="محتوای خصوصی در صف")
    staff = User.objects.create_user("review-staff@example.test", is_staff=True)
    staff.user_permissions.add(
        *Permission.objects.filter(content_type__app_label="orders", codename__in=permissions)
    )
    client.force_login(staff)
    home = client.get(reverse("admin:index")).content.decode()
    status = 200 if "view_review" in permissions else 403
    assert (HOME_LABEL in home) == (status == 200)
    assert (panel_url() in home) == (status == 200)
    for path in [panel_url(), panel_url("change", review), panel_url("history", review)]:
        result = client.get(path)
        assert result.status_code == status
        assert "انتشار نظر" not in result.content.decode()
    assert client.post(panel_url("moderate", review), PUBLISH).status_code == 403
    review.refresh_from_db()
    assert review.status == PENDING
    assert not audit(review).exists()


def test_staff_with_both_permissions_can_moderate(client, buyer, plan):
    row = review_for(buyer, plan)
    staff = User.objects.create_user("moderator@example.test", is_staff=True)
    staff.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="orders", codename__in=["view_review", "change_review"]
        )
    )
    client.force_login(staff)
    assert client.post(panel_url("moderate", row), HIDE).status_code == 302
    row.refresh_from_db()
    assert row.status == HIDDEN
    assert audit(row).get().user_id == staff.pk


def test_buyer_cannot_open_the_queue_or_moderate(client, buyer, plan):
    review = review_for(buyer, plan)
    client.force_login(buyer)
    for result in [client.get(panel_url()), client.post(panel_url("moderate", review), PUBLISH)]:
        assert result.status_code == 302
        assert reverse("admin:login") in result.url
    review.refresh_from_db()
    assert review.status == PENDING


def test_home_links_only_the_pending_count_and_disappears_at_zero(client, buyer, plan, operator):
    client.force_login(operator)
    home = reverse("admin:index")
    assert HOME_LABEL not in client.get(home).content.decode()
    pending = review_for(buyer, plan)
    second = review_for(buyer, plan)
    review_for(buyer, plan, PUBLISHED)
    review_for(buyer, plan, HIDDEN)
    body = client.get(home).content.decode()
    assert (
        f'<a href="{panel_url()}" data-tone="attention"><span>{HOME_LABEL}</span><strong>۲</strong>'
        in body
    )
    # One more tile of the order work, never a band above a late order.
    assert body.index('id="panel-work-title"') < body.index(HOME_LABEL)
    client.post(panel_url("moderate", pending), PUBLISH)
    client.post(panel_url("moderate", second), HIDE)
    assert HOME_LABEL not in client.get(home).content.decode()


def test_queue_filters_status_and_renders_read_only_escaped_reviews(client, buyer, plan, operator):
    row = review_for(buyer, plan, text="<script>bad()</script>")
    published = review_for(buyer, plan, PUBLISHED, "دیدنی است.")
    hidden = review_for(buyer, plan, HIDDEN, "کنار گذاشته شد.")
    client.force_login(operator)
    queue = client.get(panel_url())
    assert queue.status_code == 200
    for review in (published, hidden):
        assert panel_url("change", review) not in queue.content.decode()
        assert (
            panel_url("change", review)
            in client.get(panel_url(), {"status": review.status}).content.decode()
        )
    path = panel_url("change", row)
    detail = client.get(path).content.decode()
    assert "&lt;script&gt;bad()&lt;/script&gt;" in detail
    assert "<script>bad()</script>" not in detail
    assert 'name="rating"' not in detail
    assert 'name="text"' not in detail
    # A middle dot between two Persian numbers reads as a zero: «۱ از ۵ · ۱ آذر».
    assert " · <time" not in detail
    # Hiding cannot be undone, so it looks like the panel's other final actions.
    assert '<button data-danger type="submit" name="decision" value="hide"' in detail
    assert client.post(path, {"rating": 5, "text": "edited"}).status_code == 405
    assert client.post(panel_url("add"), {"rating": 5, "text": "fake"}).status_code == 403
    row.refresh_from_db()
    assert (row.rating, row.text, row.status) == (1, "<script>bad()</script>", PENDING)


def test_moderation_requires_post_with_csrf(client, buyer, plan, operator):
    row = review_for(buyer, plan)
    client.force_login(operator)
    path = panel_url("moderate", row)
    assert client.get(path).status_code == 405
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(operator)
    assert csrf.post(path, PUBLISH).status_code == 403
    assert not audit(row).exists()


@pytest.mark.parametrize(
    "data",
    [
        {**HIDE, "reason": ""},
        {**HIDE, "reason": "bad_rating"},
        {**PUBLISH, "reason": "abuse_spam"},
        {**PUBLISH, "expected_status": ""},
        {**PUBLISH, "decision": "edit"},
    ],
)
def test_bad_actions_leave_review_and_history_alone(client, buyer, plan, operator, data):
    row = review_for(buyer, plan)
    client.force_login(operator)
    result = client.post(panel_url("moderate", row), data, follow=True)
    body = result.content.decode()
    # An unknown decision or a missing reason, and a status the form never showed, each say so.
    assert "این تصمیم درباره‌ی نظر ثبت نشد" in body or "این نظر در وضعیتی نیست" in body
    row.refresh_from_db()
    assert row.status == PENDING
    assert not audit(row).exists()


def test_one_star_publishes_then_hiding_last_row_clears_public_summary(
    client, buyer, plan, operator
):
    row = review_for(buyer, plan, text="متن نظر یک‌ستاره")
    client.force_login(operator)
    visitor = Client()
    public = plan.product.get_absolute_url()
    assert "متن نظر یک‌ستاره" not in visitor.get(public).content.decode()
    post = panel_url("moderate", row)
    assert client.post(post, PUBLISH).status_code == 302
    assert (
        "وضعیت این نظر پس از باز شدن این صفحه تغییر کرده است و برای دیدن وضعیت فعلی آن، صفحه را دوباره باز کنید."
        in client.post(post, HIDE, follow=True).content.decode()
    )
    assert audit(row).count() == 1
    page = visitor.get(public).content.decode()
    assert "متن نظر یک‌ستاره" in page
    assert "۱ از ۵ · ۱ نظر" in page
    hide = {**HIDE, "expected_status": PUBLISHED, "reason": "private_information"}
    assert client.post(post, hide).status_code == 302
    client.post(post, hide)
    client.post(post, {**PUBLISH, "expected_status": HIDDEN})
    row.refresh_from_db()
    assert row.status == HIDDEN
    log = list(audit(row).order_by("pk"))
    assert [entry.user_id for entry in log] == [operator.pk, operator.pk]
    assert log[0].change_message == "Review published."
    assert log[1].change_message == "Review hidden for personal information or a sign-in secret."
    assert all(row.text not in entry.change_message + entry.object_repr for entry in log)
    history = client.get(panel_url("history", row)).content.decode()
    assert "اطلاعات شخصی یا رمز ورود" in history
    assert row.text not in history
    page = visitor.get(public).content.decode()
    assert "هنوز نظری منتشر نشده است." in page
    assert "۱ از ۵ · ۱ نظر" not in page
    assert "متن نظر یک‌ستاره" not in page


def test_audit_failure_rolls_back_the_status(client, buyer, plan, operator, monkeypatch):
    row = review_for(buyer, plan)
    client.force_login(operator)

    def failing_audit(**kwargs):
        raise RuntimeError("Audit unavailable")

    monkeypatch.setattr(LogEntry.objects, "log_actions", failing_audit)
    with pytest.raises(RuntimeError, match="Audit unavailable"):
        client.post(panel_url("moderate", row), HIDE)
    row.refresh_from_db()
    assert row.status == PENDING
    assert not audit(row).exists()


@pytest.mark.django_db(transaction=True)
def test_two_moderators_deciding_at_once_commit_one_action(buyer, plan, operator):
    row = review_for(buyer, plan)
    clients = [Client(), Client()]
    for client in clients:
        client.force_login(operator)
    post = panel_url("moderate", row)
    pids = Queue()
    with ThreadPoolExecutor(1) as executor:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                holder = cursor.fetchone()[0]
            assert clients[0].post(post, PUBLISH).status_code == 302
            hide = executor.submit(in_connection, pids, lambda: clients[1].post(post, HIDE))
            wait_for_lock(pids.get(timeout=6), holder)
        assert hide.result(timeout=15).status_code == 302

    row.refresh_from_db()
    assert row.status == PUBLISHED
    assert audit(row).count() == 1

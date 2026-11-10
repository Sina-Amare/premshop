from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core import telegram as telegram_api
from apps.core.email_log import MailQuotaExceeded
from apps.notifications import services
from apps.notifications.models import ConfirmationSweep, Notification
from apps.notifications.tasks import send_order_notifications
from apps.orders import services as orders
from apps.orders.tests.test_input_clock import operator as operator
from apps.payments.services import record_manual_payment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def telegram(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = (
        "123456789:AAEexampleSECRETexampleSECRETexample12"  # noqa: S105  # nosec B105
    )
    fake = SimpleNamespace(sent=[], error=None)

    def send_message(chat_id, text, **kwargs):
        fake.sent.append((chat_id, text))
        if fake.error:
            raise fake.error
        return {"message_id": 1}

    monkeypatch.setattr(telegram_api, "send_message", send_message)
    return fake


def linked(user, telegram_id=555):
    User.objects.filter(pk=user.pk).update(telegram_id=telegram_id)


def placed(buyer, plan):
    return orders.place_order(user=buyer, lines=[orders.OrderLine(plan, 1)])


@pytest.fixture
def queued(buyer, plan, telegram):
    linked(buyer)
    services.enqueue_event("payment_confirmed", 50, placed(buyer, plan).pk)
    return Notification.objects.get(channel="telegram")


def test_a_linked_customer_gets_an_email_row_and_a_telegram_row(
    buyer, plan, operator, telegram, settings
):
    settings.SITE_URL = "https://shop.example.test"
    linked(buyer)
    order = placed(buyer, plan)
    record_manual_payment(
        order_id=order.pk, actor=operator, amount=order.total_amount, bank_ref="PRIVATE-BANK"
    )
    email = Notification.objects.get(template_name="payment_confirmed", channel="email")
    row = Notification.objects.get(template_name="payment_confirmed", channel="telegram")
    assert row.dedupe_key == email.dedupe_key.removesuffix(":email") + ":telegram"
    assert (row.chat_id, row.recipient, row.subject, row.html_body) == (555, "", "", "")
    url = settings.SITE_URL + reverse("order-detail", args=[order.order_number])
    assert row.text_body == f"{email.subject}\n{url}"
    assert "PRIVATE-BANK" not in row.text_body


def test_an_unlinked_customer_or_a_shop_without_a_bot_gets_email_only(
    buyer, plan, telegram, settings
):
    order = placed(buyer, plan)
    services.enqueue_event("payment_confirmed", 51, order.pk)
    settings.TELEGRAM_BOT_TOKEN = ""
    linked(buyer)
    services.enqueue_event("payment_confirmed", 52, order.pk)
    assert set(Notification.objects.values_list("channel", flat=True)) == {"email"}


def test_a_repeated_occurrence_adds_no_row(buyer, plan, telegram):
    linked(buyer)
    order = placed(buyer, plan)
    for _ in range(2):
        services.enqueue_event("payment_confirmed", 53, order.pk)
    assert sorted(Notification.objects.values_list("channel", flat=True)) == ["email", "telegram"]


def test_the_telegram_text_is_escaped(buyer, plan, telegram, monkeypatch):
    linked(buyer)
    monkeypatch.setattr(services, "render_email", lambda name, context: ("<b>&", "t", "<p>h</p>"))
    services.enqueue_event("payment_confirmed", 54, placed(buyer, plan).pk)
    assert Notification.objects.get(channel="telegram").text_body.startswith("&lt;b&gt;&amp;\n")


def test_a_telegram_row_is_sent(queued, telegram):
    assert services.send_due_telegram() == 1
    queued.refresh_from_db()
    assert queued.status == "sent" and queued.sent_at is not None
    assert telegram.sent == [(555, queued.text_body)]


@pytest.mark.parametrize(
    ("status", "retry_after", "outcome", "least_wait"),
    [
        (429, 900, "pending", 900),
        (403, None, "failed", None),
        (400, None, "failed", None),
        (401, None, "pending", 60),
        (502, None, "pending", 60),
        (None, None, "pending", 60),
    ],
)
def test_telegram_failures_follow_their_rules(
    queued, telegram, status, retry_after, outcome, least_wait
):
    telegram.error = telegram_api.TelegramError(status, retry_after)
    services.send_due_telegram()
    queued.refresh_from_db()
    assert (queued.status, queued.attempts) == (outcome, 1)
    assert queued.last_error_class == f"TelegramError {status or 'network'}"
    if least_wait is None:
        assert queued.next_attempt_at is None
    else:
        assert queued.next_attempt_at >= queued.last_attempt_at + timedelta(seconds=least_wait)


def test_the_ladder_still_ends_after_six_attempts(queued, telegram):
    telegram.error = telegram_api.TelegramError(429, 1)
    Notification.objects.filter(pk=queued.pk).update(attempts=5)
    services.send_due_telegram()
    queued.refresh_from_db()
    assert (queued.status, queued.attempts, queued.next_attempt_at) == ("failed", 6, None)


@pytest.mark.parametrize("now_linked_to", [None, 777])
def test_a_changed_link_receives_nothing(queued, telegram, buyer, now_linked_to):
    linked(buyer, now_linked_to)
    services.send_due_telegram()
    queued.refresh_from_db()
    assert (queued.status, queued.last_error_class) == ("failed", "binding_changed")
    assert telegram.sent == []


def test_the_email_quota_wait_leaves_telegram_alone(queued, telegram, monkeypatch):
    def full(*args, **kwargs):
        raise MailQuotaExceeded

    monkeypatch.setattr(services, "send_templated_email", full)
    services.send_due_notifications()
    email = Notification.objects.get(channel="email")
    assert email.next_attempt_at > timezone.now() + timedelta(minutes=14)
    queued.refresh_from_db()
    assert queued.next_attempt_at <= timezone.now()
    ConfirmationSweep.objects.filter(pk=1).update(
        quota_wait_until=timezone.now() + timedelta(minutes=15)
    )
    send_order_notifications.call_local()
    queued.refresh_from_db()
    assert queued.status == "sent"
    assert set(Notification.objects.filter(channel="email").values_list("status", flat=True)) == {
        "pending"
    }

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.core.email_log import fingerprint
from apps.core.formatting import format_jalali
from apps.core.models import MailAttempt
from apps.notifications.models import Notification
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(client):
    user = get_user_model().objects.create_superuser("panel@example.test")
    client.force_login(user)
    return user


def gregorian_months(body: str) -> list[str]:
    names = ("نوامبر", "دسامبر", "ژانویه", "November", "December", "January")
    return [name for name in names if name in body]


def test_an_order_messages_page_reads_in_the_panels_own_terms(client, operator):
    order = Order.objects.create(user=operator, subtotal=0, total_amount=0)
    message = Notification.objects.create(
        order=order,
        dedupe_key=f"order:{order.pk}:placed:email",
        template_name=Notification.Template.values[0],
        channel=Notification.Channel.EMAIL,
        recipient="buyer@example.test",
        next_attempt_at=timezone.now(),
    )
    url = reverse("admin:notifications_notification_change", args=[message.pk])
    body = client.get(url).content.decode()
    assert format_jalali(message.created_at) in body
    assert not gregorian_months(body)
    assert "dedupe key" not in body.lower() and "کلید یکتای پیام" in body


def test_a_mail_attempts_page_dates_its_moments_in_jalali(client, operator):
    attempt = MailAttempt.objects.create(
        template="password_reset",
        recipient_hash=fingerprint("buyer@example.test"),
        status=MailAttempt.Status.ACCEPTED,
        completed_at=timezone.now(),
    )
    url = reverse("admin:core_mailattempt_change", args=[attempt.pk])
    body = client.get(url).content.decode()
    assert format_jalali(attempt.created_at) in body
    assert not gregorian_months(body)


def test_the_order_message_list_counts_in_persian_digits(client, operator):
    order = Order.objects.create(user=operator, subtotal=0, total_amount=0)
    Notification.objects.create(
        order=order,
        dedupe_key=f"order:{order.pk}:list:email",
        template_name=Notification.Template.values[0],
        channel=Notification.Channel.EMAIL,
        recipient="buyer@example.test",
        next_attempt_at=timezone.now(),
    )
    body = client.get(reverse("admin:notifications_notification_changelist")).content.decode()
    assert re.search(r"field-attempts_shown[^>]*>\s*۰\s*<", body)


def test_a_message_page_prints_its_counts_and_codes_in_persian_digits(client, operator):
    order = Order.objects.create(user=operator, subtotal=0, total_amount=0)
    row = Notification.objects.create(
        order=order,
        dedupe_key=f"order:{order.pk}:detail:telegram",
        template_name=Notification.Template.values[0],
        channel=Notification.Channel.TELEGRAM,
        chat_id=987654321,
        attempts=3,
        smtp_code=550,
        next_attempt_at=timezone.now(),
    )
    url = reverse("admin:notifications_notification_change", args=[row.pk])
    body = client.get(url).content.decode()
    assert "۵۵۰" in body and not re.search(r">\s*(3|550)\s*<", body)
    # A chat id is a code to copy, not a count to read: it stays as Telegram writes it.
    assert "987654321" in body

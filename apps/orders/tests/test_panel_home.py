import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.formatting import to_persian_digits
from apps.orders.tests.test_input_clock import operator as operator
from apps.orders.tests.test_order_detail import order_for_state

pytestmark = pytest.mark.django_db(transaction=True)


def tiles(body: str) -> dict[str, tuple[str, str, str]]:
    """Each order-work tile on the panel's home: its tone, count and link, by its words."""
    work = body.split('class="panel-work"', 1)[1].split("</section>", 1)[0]
    found = re.findall(
        r'<a href="([^"]+)" data-tone="([a-z]+)"><span>([^<]+)</span><strong>([^<]+)</strong>',
        work,
    )
    return {label: (tone, count, url) for url, tone, label, count in found}


def test_the_panels_home_counts_the_order_work_in_its_tones(client, buyer, plan, operator):
    late = order_for_state(buyer, plan, operator, "queued")
    late.items.update(due_at=timezone.now() - timedelta(minutes=5))
    order_for_state(buyer, plan, operator, "owed")
    client.force_login(operator)
    found = tiles(client.get(reverse("admin:index")).content.decode())
    one, none = to_persian_digits(1), to_persian_digits(0)
    queue = reverse("admin:orders_order_changelist")
    assert found["دیر شده"] == ("problem", one, f"{queue}?tab=queued")
    assert found["در صف انجام"] == ("progress", one, f"{queue}?tab=queued")
    assert found["بازپرداخت در انتظار واریز"][:2] == ("attention", one)
    # Nothing waiting reads grey, so colour marks only work that exists.
    assert found["منتظر پرداخت"] == ("closed", none, f"{queue}?tab=unpaid")
    assert found["درخواست تعویض"][:2] == ("closed", none)


def test_the_queue_raises_the_expiry_check_only_when_something_is_wrong(
    client, buyer, plan, operator
):
    from apps.orders.models import UnpaidExpirySweep

    UnpaidExpirySweep.objects.create(
        last_success_at=timezone.now(), processed_count=0, overdue_count=0
    )
    client.force_login(operator)
    queue = reverse("admin:orders_order_changelist")
    healthy = client.get(queue).content.decode()
    assert 'class="order-expiry"' not in healthy
    assert "آخرین بررسی مهلت پرداخت" in healthy.split('class="order-queue-as-of"', 1)[1][:600]
    UnpaidExpirySweep.objects.update(last_success_at=timezone.now() - timedelta(hours=1))
    stale = client.get(queue).content.decode()
    assert '<section class="order-expiry" data-tone="problem"' in stale
    assert "بررسی خودکار بیش از ۳ دقیقه است که اجرا نشده" in stale


def test_a_search_looks_through_every_order_whatever_the_tab(client, buyer, plan, operator):
    owed = order_for_state(buyer, plan, operator, "owed")
    client.force_login(operator)
    queue = reverse("admin:orders_order_changelist")
    body = client.get(queue, {"tab": "queued", "q": str(owed.order_number)}).content.decode()
    assert reverse("admin:orders_order_change", args=[owed.pk]) in body

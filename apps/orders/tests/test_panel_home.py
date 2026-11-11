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
    assert found["بازگشت وجه در انتظار واریز"][:2] == ("attention", one)
    # Nothing waiting reads grey, so colour marks only work that exists.
    assert found["منتظر پرداخت"] == ("closed", none, f"{queue}?tab=unpaid")
    assert found["درخواست تعویض"][:2] == ("closed", none)

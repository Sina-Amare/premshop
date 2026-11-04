"""A request the live server is still answering when a test ends finishes before the flush.

Otherwise the flush that empties the database races it: TRUNCATE deadlocks against
the request's own queries, or the request writes after the flush and its row is
waiting in the next test. The two tests run in file order; the second one fails.
"""

import threading
import time
import urllib.request

import pytest
from django.http import HttpResponse
from django.urls import path

from apps.catalog.models import Category
from config import urls

pytestmark = pytest.mark.django_db(transaction=True)

arrived, finished = threading.Event(), threading.Event()
outcome: list[str] = []


def slow_write(request):
    arrived.set()
    try:
        time.sleep(1)
        Category.objects.create(name="دیر رسیده", slug="late")
        outcome.append("written")
    except Exception as error:
        outcome.append(repr(error))
        raise
    finally:
        finished.set()
    return HttpResponse("done")


urlpatterns = [path("slow-write/", slow_write), *urls.urlpatterns]


def test_a_test_may_end_while_its_request_is_still_running(live_server, settings):
    settings.ROOT_URLCONF = __name__
    threading.Thread(
        target=urllib.request.urlopen, args=(live_server.url + "/slow-write/",), daemon=True
    ).start()
    assert arrived.wait(10)


def test_the_next_test_starts_without_that_requests_row(live_server):
    assert finished.wait(
        10
    ), "the first test starts the request this one waits for: run the whole file"
    assert outcome == ["written"]
    assert not Category.objects.filter(slug="late").exists()

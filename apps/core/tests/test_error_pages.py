"""The pages a visitor sees when something goes wrong, in the shop's own frame.

Django ships an English 403 and an English CSRF page, and a 500 rendered
without any request context: no hours, no handle, nothing to do next. Each
of these is the moment a buyer decides whether a shop that took their money
is still there.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.views.defaults import permission_denied

from apps.accounts.models import User
from apps.core import support

pytestmark = pytest.mark.django_db

ENGLISH = ("Forbidden", "CSRF", "Server Error", "Bad Request", "Not Found")


def test_a_permission_error_is_a_persian_page(client):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()

    response = permission_denied(request, PermissionDenied())
    body = response.content.decode()

    assert response.status_code == 403
    assert 'lang="fa"' in body
    assert "اجازه‌ی دیدن این صفحه را ندارید" in body
    assert not [word for word in ENGLISH if word in body]


def test_a_stale_form_is_a_persian_page_with_a_way_back(db):
    """A form whose token went stale, most often across a sign-in in another
    tab. Django's own page is English and talks about referers to someone who
    wanted to sign in."""
    client = Client(enforce_csrf_checks=True)

    response = client.post(reverse("login"), {"email": "a@example.test", "password": "x"})
    body = response.content.decode()

    assert response.status_code == 403
    assert "این فرم دیگر کار نمی‌کند" in body
    assert 'href="/"' in body, "a dead end is not an error page"
    assert not [word for word in ENGLISH if word in body]


def test_a_stale_form_in_the_panel_leads_back_to_the_panel(db):
    """The operator's forms fail the same way, and the way back from one of
    them is the panel, not the shop's product list."""
    operator = User.objects.create_user("operator@example.test", "x", is_staff=True)
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)

    response = client.post(reverse("admin:catalog_category_add"), {"name": "x"})
    body = response.content.decode()

    assert response.status_code == 403
    assert "این فرم دیگر کار نمی‌کند" in body
    assert f'href="{reverse("admin:index")}"' in body
    assert "بازگشت به فهرست محصولات" not in body


def test_the_500_page_needs_nothing_that_might_be_what_failed(client):
    """Rendered without a request context: a database outage is the likeliest
    cause of a 500, so the page touches no table, and the support details come
    from their one definition instead of a context processor."""
    client.raise_request_exception = False

    with (
        mock.patch("apps.catalog.views.render", side_effect=RuntimeError("boom")),
        CaptureQueriesContext(connection) as queries,
    ):
        response = client.get(reverse("faq"))
    body = response.content.decode()

    assert response.status_code == 500
    assert not [q["sql"] for q in queries if "catalog_" in q["sql"]], "the page queried the catalog"
    details = support.details()
    assert details["telegram"] in body
    assert details["hours"] in body
    assert "دوباره پرداخت نکنید" in body
    assert not [word for word in ENGLISH if word in body]


@pytest.mark.parametrize("url", ["/p/%00/", "/c/%00x/", "/search/?q=a%00b", "/%00"])
def test_a_nul_in_the_address_is_refused_before_the_database(client, url):
    """PostgreSQL refuses a NUL inside a string, so it used to reach the
    database and come back as a 500 on any URL that queried with it."""
    client.raise_request_exception = False

    response = client.get(url)

    assert response.status_code == 404
    assert "این صفحه رو پیدا نکردیم" in response.content.decode()


def test_a_nul_in_the_session_cookie_never_reaches_the_database(client, settings):
    """http.cookies reads `\\000` in a quoted cookie as a NUL, and the session
    lookup carried it to PostgreSQL: a 500, on any page, as often as sent."""
    client.raise_request_exception = False

    response = client.get(
        reverse("faq"), HTTP_COOKIE=f'{settings.SESSION_COOKIE_NAME}="abcdefghij\\000"'
    )

    assert response.status_code == 200


class TestStyleguide:
    def test_is_hidden_from_visitors_in_production(self, client, settings):
        settings.DEBUG = False

        assert client.get(reverse("styleguide")).status_code == 404

    def test_is_open_to_staff(self, client, settings):
        settings.DEBUG = False
        client.force_login(User.objects.create_user("op@example.test", is_staff=True))

        assert client.get(reverse("styleguide")).status_code == 200

    def test_is_open_in_development(self, client, settings):
        settings.DEBUG = True

        assert client.get(reverse("styleguide")).status_code == 200

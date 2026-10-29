from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.templatetags.static import static as static_url
from django.urls import include, path

from apps.core import views as core_views


def staff_or_debug(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """A review page, not part of the shop: a 404 for anyone else in production."""

    @wraps(view)
    def guarded(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not (settings.DEBUG or request.user.is_staff):
            raise Http404
        return view(request, *args, **kwargs)

    return guarded


def favicon(request: HttpRequest) -> HttpResponse:
    """Browsers and crawlers ask for /favicon.ico whatever the page links to."""
    return redirect(static_url("img/favicon.ico"))


urlpatterns = [
    path("favicon.ico", favicon),
    path("", include("apps.catalog.urls")),
    path("", include("apps.cms.urls")),
    # set_language, which the operator panel's language switch posts to. Unfold
    # reverses this name on every sidebar render, so without it the whole admin
    # raises NoReverseMatch the moment SHOW_LANGUAGES is on.
    path("i18n/", include("django.conf.urls.i18n")),
    # The design system rendered, for review. Not linked from the shop.
    path("styleguide/", staff_or_debug(core_views.styleguide), name="styleguide"),
    path("healthz", core_views.healthz, name="healthz"),
    # Development-only email previews; the view itself 404s when DEBUG is off.
    path("dev/emails/", core_views.email_preview, name="email-previews"),
    path("dev/emails/<str:name>/", core_views.email_preview, name="email-preview"),
    path("", include("apps.accounts.urls")),
    path("cart/", include("apps.cart.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.orders.urls")),
    path("admin/", admin.site.urls),
]

handler500 = "apps.core.errors.server_error"

# Product images in development only. In production the reverse proxy serves
# MEDIA_ROOT directly and this list is empty because DEBUG is False.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

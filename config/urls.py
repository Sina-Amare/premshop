from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("", include("apps.catalog.urls")),
    # The design system rendered, for review. Not linked from the shop.
    path("styleguide/", core_views.styleguide, name="styleguide"),
    path("healthz", core_views.healthz, name="healthz"),
    # Development-only email previews; the view itself 404s when DEBUG is off.
    path("dev/emails/", core_views.email_preview, name="email-previews"),
    path("dev/emails/<str:name>/", core_views.email_preview, name="email-preview"),
    path("", include("apps.accounts.urls")),
    path("admin/", admin.site.urls),
]

# Product images in development only. In production the reverse proxy serves
# MEDIA_ROOT directly and this list is empty because DEBUG is False.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

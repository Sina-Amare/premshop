from django.contrib import admin
from django.urls import path

from apps.core import views as core_views

urlpatterns = [
    path("", core_views.styleguide, name="styleguide"),
    path("healthz", core_views.healthz, name="healthz"),
    # Development-only email previews; the view itself 404s when DEBUG is off.
    path("dev/emails/", core_views.email_preview, name="email-previews"),
    path("dev/emails/<str:name>/", core_views.email_preview, name="email-preview"),
    path("admin/", admin.site.urls),
]

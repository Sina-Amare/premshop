from django.contrib import admin
from django.urls import path

from apps.core import views as core_views

urlpatterns = [
    path("", core_views.styleguide, name="styleguide"),
    path("healthz", core_views.healthz, name="healthz"),
    path("admin/", admin.site.urls),
]

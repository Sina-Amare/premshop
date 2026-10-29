from django.urls import path

from apps.cms import views
from apps.cms.models import Page

app_name = "cms"
urlpatterns = [path(f"{slug}/", views.page, {"slug": slug}, name=slug) for slug in Page.Slug.values]

from django.urls import path

from apps.catalog import views

# <str:slug> rather than <slug:slug>: category and product slugs may be Persian
# («ابزار-توسعه»), and Django's slug converter is ASCII-only.
urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("c/<str:slug>/", views.category, name="category"),
    path("p/<str:slug>/", views.product, name="product"),
]

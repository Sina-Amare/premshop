from django.urls import path

from apps.catalog import views

# <str:slug> rather than <slug:slug>: new slugs are Latin, but a row saved
# before that rule may still carry a Persian one («ابزار-توسعه»), and Django's
# slug converter is ASCII-only.
urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("search/suggest/", views.search_suggest, name="search-suggest"),
    path("faq/", views.faq, name="faq"),
    path("c/<str:slug>/", views.category, name="category"),
    path("p/<str:slug>/", views.product, name="product"),
]

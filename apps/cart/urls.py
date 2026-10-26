from django.urls import path

from apps.cart import views

urlpatterns = [
    path("", views.detail, name="cart"),
    path("add/", views.add, name="cart-add"),
    path("quantity/", views.quantity, name="cart-quantity"),
    path("remove/", views.remove, name="cart-remove"),
    path("availability/", views.availability, name="cart-availability"),
]

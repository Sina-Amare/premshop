from django.urls import path

from apps.orders import views

urlpatterns = [
    path("account/orders/", views.order_list, name="order-list"),
    path("account/orders/<int:order_number>/", views.detail, name="order-detail"),
    path("account/orders/<int:order_number>/cancel/", views.cancel, name="order-cancel"),
    path("account/orders/<int:order_number>/invoice/", views.invoice, name="order-invoice"),
    path("track/<str:token>/", views.tracking, name="order-tracking"),
]

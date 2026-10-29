from django.urls import path

from apps.orders import views

urlpatterns = [path("account/orders/<int:order_number>/", views.detail, name="order-detail")]

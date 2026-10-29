from django.urls import path

from apps.payments import views

urlpatterns = [path("checkout/", views.checkout, name="checkout")]

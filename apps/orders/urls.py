from django.urls import path

from apps.orders import views

urlpatterns = [
    path("account/orders/", views.order_list, name="order-list"),
    path("account/orders/<int:order_number>/", views.detail, name="order-detail"),
    path(
        "account/orders/<int:order_number>/items/<int:item_id>/claim/",
        views.file_claim,
        name="order-claim",
    ),
    path(
        "account/orders/<int:order_number>/delivery/code/",
        views.delivery_code,
        name="order-delivery-code",
    ),
    path(
        "account/orders/<int:order_number>/delivery/confirm/",
        views.delivery_code,
        {"confirm": True},
        name="order-delivery-confirm",
    ),
    path(
        "account/orders/<int:order_number>/items/<int:item_id>/delivery/",
        views.delivery_reveal,
        name="order-delivery-reveal",
    ),
    path(
        "account/orders/<int:order_number>/items/<int:item_id>/review/",
        views.submit_review,
        name="order-review",
    ),
    path("account/orders/<int:order_number>/cancel/", views.cancel, name="order-cancel"),
    path(
        "account/orders/<int:order_number>/items/<int:item_id>/input/",
        views.provide_input,
        name="order-input-submit",
    ),
    path(
        "account/orders/<int:order_number>/support/replace/",
        views.replace_support,
        name="order-support-replace",
    ),
    path("account/orders/<int:order_number>/invoice/", views.invoice, name="order-invoice"),
    path("track/<str:token>/", views.tracking, name="order-tracking"),
]

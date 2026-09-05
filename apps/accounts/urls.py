from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("login/", views.login_password, name="login"),
    path("login/code/", views.login_code_request, name="login-code"),
    path("login/code/verify/", views.login_code_verify, name="login-code-verify"),
    path("login/code/resend/", views.login_code_resend, name="login-code-resend"),
    path("logout/", views.logout, name="logout"),
    path("account/", views.account, name="account"),
]

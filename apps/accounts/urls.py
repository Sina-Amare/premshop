from django.urls import path
from django.views.generic import RedirectView

from apps.accounts import views

urlpatterns = [
    path("login/", views.login_password, name="login"),
    path("login/code/", views.login_code_request, name="login-code"),
    path("login/code/verify/", views.login_code_verify, name="login-code-verify"),
    path("login/code/resend/", views.login_code_resend, name="login-code-resend"),
    path("logout/", views.logout, name="logout"),
    path("account/", views.account, name="account"),
    # Merged into /account/ on 2026-10-14. Kept as a redirect rather than
    # deleted: the address has been live, and a 404 on a page someone
    # bookmarked is a worse answer than the page they wanted.
    path(
        "account/profile/",
        RedirectView.as_view(pattern_name="account", permanent=False),
        name="account-profile",
    ),
    path("account/password/", views.account_password, name="account-password"),
]

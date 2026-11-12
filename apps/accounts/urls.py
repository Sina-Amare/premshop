from django.urls import path
from django.views.generic import RedirectView

from apps.accounts import views

urlpatterns = [
    # A customer signs in by code only, so the sign-in address opens on the code
    # form. The query string goes along as it came; the code form's _safe_next
    # decides what `next` may do.
    path(
        "login/",
        RedirectView.as_view(pattern_name="login-code", query_string=True, permanent=False),
        name="login",
    ),
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
    path("account/telegram/", views.account_telegram, name="account-telegram"),
    path("account/telegram/state/", views.account_telegram_state, name="account-telegram-state"),
]

"""The mark a sign-in code leaves on its session.

The operator panel lets in any active staff session, and Django's session does not
say how it was opened. A session a code opened is logged in under this backend, so
the panel can refuse it, even on an account made staff after the code was used.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.backends import ModelBackend
from django.http import HttpRequest

CODE_SIGN_IN = "apps.accounts.backends.CodeSignInBackend"


class CodeSignInBackend(ModelBackend):
    """Checks no credential itself: it names the sessions a code opened, and loads
    their user as Django's own backend does."""

    def authenticate(self, request: HttpRequest | None, *args: Any, **kwargs: Any) -> None:
        return None

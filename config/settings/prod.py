"""Production settings.

Used from the soft-deploy step (S6b). Everything here fails closed: if a
required secret is missing the process refuses to start rather than running
with an insecure default.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env, env_list

DEBUG = False

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")

# HTTPS everywhere; the proxy terminates TLS and forwards the scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# base.py already defaults EMAIL_BACKEND to SMTP. What must not be inherited is a
# blank credential: Django's SMTP backend guards login() with `if self.username and
# self.password:`, so an empty password connects ANONYMOUSLY and the relay rejects
# every message. That is a silent total login outage, so it becomes a refusal to boot.
EMAIL_HOST_USER = env("EMAIL_HOST_USER", required=True)
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", required=True)

# SMTP2GO accepts only mailboxes at the verified sender domain; anything else is
# rejected after the quota is spent. Assert it here rather than discovering it via
# a customer who never got their code.
if not DEFAULT_FROM_EMAIL.strip().rstrip(">").endswith("@premshop.ir"):  # noqa: F405
    raise ImproperlyConfigured(
        "DEFAULT_FROM_EMAIL must be a mailbox at premshop.ir, the SMTP2GO-verified "
        f"sender domain. Got: {DEFAULT_FROM_EMAIL!r}"  # noqa: F405
    )

# Error reporting: sentry-sdk speaking to self-hosted GlitchTip (ADR-0015).
# No DSN configured means no reporting — never a crash at startup.
if env("SENTRY_DSN"):
    import sentry_sdk

    from apps.core.observability import scrub_event

    sentry_sdk.init(
        dsn=env("SENTRY_DSN"),
        environment=env("SENTRY_ENVIRONMENT", "production"),
        # Never attach user identity or request bodies (ADR-0007).
        send_default_pii=False,
        max_request_body_size="never",
        before_send=scrub_event,
        traces_sample_rate=0.0,
    )

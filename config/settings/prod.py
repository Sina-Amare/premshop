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

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

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

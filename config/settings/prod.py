"""Production settings.

Used from the soft-deploy step (S6b). Everything here fails closed: if a
required secret is missing the process refuses to start rather than running
with an insecure default.
"""

import re

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env, env_list, site_url

DEBUG = False

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")

# HTTPS everywhere; the proxy terminates TLS and forwards the scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
# An hour to start with: a certificate mistake then locks visitors out for an hour,
# not a year. Django sends no header at all for 0 and an invalid max-age for a negative
# number, so both stop the boot, as does a value that is not a whole number.
try:
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS") or "3600")
    if SECURE_HSTS_SECONDS < 1:
        raise ValueError
except ValueError:
    raise ImproperlyConfigured(
        "SECURE_HSTS_SECONDS must be a whole number of seconds, at least 1."
    ) from None
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False  # The launch step (S12) raises the seconds and turns this on.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Every static file under a name carrying its content hash, so nginx can cache it
# for a year: a changed file is a new name, never a stale copy.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
# No script reads the token from the cookie: each form carries it in a hidden field.
CSRF_COOKIE_HTTPONLY = True
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# web publishes no port (deploy/compose.yaml), so every request reaches it through
# nginx, which overwrites X-Real-IP with the address it saw (apps/core/ratelimit.py).
TRUST_PROXY_HEADERS = True

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

# Without it every canonical link, preview image and email link would be built on a
# development address, and nothing on the page would look broken.
SITE_URL = site_url(env("SITE_URL", required=True))
if not SITE_URL.startswith("https://"):
    raise ImproperlyConfigured(f"SITE_URL must be https in production. Got: {SITE_URL!r}")

# A missing key would otherwise surface as a 500 on the first order that needs input.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", required=True)
field_keys = [FIELD_ENCRYPTION_KEY, *env_list("FIELD_ENCRYPTION_KEY_FALLBACKS")]
try:
    for key in field_keys:
        Fernet(key.encode())
    if len(set(field_keys)) != len(field_keys):
        raise ValueError
except ValueError:
    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY and its fallbacks must be valid, distinct Fernet keys."
    ) from None

# The panel away from the address every scanner tries first. The sign-in limits
# are still its guard; this keeps it out of the noise.
ADMIN_PATH = env("ADMIN_PATH", required=True)
if ADMIN_PATH.lower() == "admin/" or not re.fullmatch(r"[A-Za-z0-9_-]+/", ADMIN_PATH):
    raise ImproperlyConfigured(
        "ADMIN_PATH must be one path segment ending in a slash, other than admin/."
    )

# Error reporting: sentry-sdk speaking to self-hosted GlitchTip (ADR-0015).
# No DSN configured means no reporting — never a crash at startup.
if env("SENTRY_DSN"):
    import sentry_sdk

    from apps.core.observability import sentry_options

    sentry_sdk.init(
        **sentry_options(
            dsn=env("SENTRY_DSN"),
            environment=env("SENTRY_ENVIRONMENT", "production"),
            release=GIT_COMMIT,  # noqa: F405
        )
    )

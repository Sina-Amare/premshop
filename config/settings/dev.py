"""Local development settings."""

from .base import *  # noqa: F403
from .base import env, env_bool

DEBUG = env_bool("DEBUG", True)

# Console by default so routine local work never spends relay quota or mails a real
# person. Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend in .env to
# drive the real SMTP2GO relay from this machine — that is how ADR-0022 was proven.
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")

INTERNAL_IPS = ["127.0.0.1"]

"""Local development settings."""

from .base import *  # noqa: F403
from .base import env_bool

DEBUG = env_bool("DEBUG", True)

# Email goes to the console until a provider is chosen (S2 gate, open-questions #1).
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

INTERNAL_IPS = ["127.0.0.1"]

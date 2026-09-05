"""Settings shared by every environment.

Configuration comes from environment variables (12-factor): the same code
artifact runs everywhere and only its environment differs. Values that vary
per environment live in .env locally and in the process environment on
servers; .env is never committed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured
from django.core.mail.utils import DNS_NAME

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=value lines from .env without overriding the real environment.

    setdefault matters: on a server the process environment wins, so a stale
    .env can never silently override deployed configuration.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def env(key: str, default: str = "", *, required: bool = False) -> str:
    value = os.environ.get(key, default)
    if required and not value:
        raise ImproperlyConfigured(
            f"Missing required environment variable: {key}. See .env.example."
        )
    return value


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(key, default).split(",") if item.strip()]


def database_from_url(url: str) -> dict[str, Any]:
    """Parse postgres://user:password@host:port/name into Django's DATABASES entry."""
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(f"DATABASE_URL must be a postgres:// URL, got {parsed.scheme!r}")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        "CONN_MAX_AGE": 60,
    }


_load_dotenv(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", required=True)
DEBUG = env_bool("DEBUG")
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    # unfold must precede django.contrib.admin: it overrides the admin templates,
    # and Django resolves templates in INSTALLED_APPS order.
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.catalog",
    "apps.core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Before everything else that might touch the cache, so its 503 wins over a
    # traceback no matter which view raised.
    "apps.core.middleware.CacheUnavailableMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": database_from_url(env("DATABASE_URL", required=True))}

REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")

# OTP codes and rate-limit counters go through Django's cache API rather than a
# raw redis client. Two reasons: an expiring code should evaporate on its own
# instead of accumulating as dead rows someone has to sweep, and the cache API
# swaps to locmem in tests automatically, so the suite needs no running Redis.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# --- Email (ADR-0022). Transactional relay: SMTP2GO. ---
# Email is the login system: a lost OTP is a customer who cannot reach what they
# paid for. Everything here is env-driven so switching relay or port is a restart,
# not a deploy — which is also why there is no provider abstraction (ADR-0013).
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "mail-eu.smtp2go.com")
EMAIL_PORT = int(env("EMAIL_PORT", "2525"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")

# The port is the only encryption knob. SMTP2GO speaks implicit TLS on 465/8465/443
# and STARTTLS everywhere else; Django raises ValueError when both flags are true,
# so deriving them from the port makes that mistake unrepresentable rather than
# merely documented. Never set EMAIL_USE_TLS/EMAIL_USE_SSL in .env.
EMAIL_USE_SSL = EMAIL_PORT in {465, 8465, 443}
EMAIL_USE_TLS = not EMAIL_USE_SSL

# Django's default is None — no timeout at all. An Iran-to-Europe SMTP connection
# that blackholes would otherwise pin a web worker forever on the login path.
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "10"))

# Must be a mailbox at the SMTP2GO-verified sender domain or the relay rejects the
# message and still spends quota. support@ over noreply@: it has working inbound
# forwarding, so a customer who replies is heard instead of bouncing into nothing.
# Latin brand name in the From: it is the one anchor in an inbox full of Persian
# subjects, and the owner's ruling is one spelling everywhere — "PremShop".
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "PremShop <support@premshop.ir>")
SERVER_EMAIL = env("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# The address customers are told to write to, and the Reply-To on every message.
# Separate from DEFAULT_FROM_EMAIL, which carries a display name and may change.
SUPPORT_EMAIL = env("SUPPORT_EMAIL", "support@premshop.ir")

# Django derives two things from socket.getfqdn(): the Message-ID domain and the SMTP
# EHLO name. Left alone it stamps the machine's own hostname into every customer's
# inbox, and its own source warns the lookup "can take a couple of seconds" — it runs
# before the socket exists, so EMAIL_TIMEOUT cannot bound it. Pinning the cache slot
# pre-empts the lookup entirely: no reverse DNS, no hostname leak, no stall.
DNS_NAME._fqdn = env("EMAIL_HELO_NAME", "premshop.ir")

# Set BEFORE any model in this project exists. Django allows swapping the user
# model only while the database has no tables; afterwards it is a hand-rebuilt
# schema on a live system. accounts.0001 is migration one of the whole history.
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/account/"
LOGOUT_REDIRECT_URL = "/"

# Django's rules, with the error sentences in this shop's words — its own Persian
# says «گذرواژه» where every screen here says «رمز عبور» (apps/accounts/validators.py).
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "apps.accounts.validators.UserAttributeSimilarityValidator"},
    {"NAME": "apps.accounts.validators.MinimumLengthValidator"},
    {"NAME": "apps.accounts.validators.CommonPasswordValidator"},
    {"NAME": "apps.accounts.validators.NumericPasswordValidator"},
]

# Persian UI; datetimes are stored in UTC and rendered in Tehran time.
# Jalali is a presentation concern only (apps.core.formatting).
LANGUAGE_CODE = "fa-ir"
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# The admin is the operator's tool until S7 builds the panel proper. Unfold makes
# Django's admin usable in RTL; the site name is the brand, in Latin, everywhere.
UNFOLD = {
    "SITE_TITLE": "PremShop",
    "SITE_HEADER": "PremShop",
    "SITE_SYMBOL": "storefront",
    "SHOW_HISTORY": True,
    # The brand's teal as the primary, in the tint ramp Unfold expects.
    "COLORS": {
        "primary": {
            "50": "240 249 248", "100": "204 236 233", "200": "153 217 211", "300": "102 198 190",
            "400": "51 179 168", "500": "15 118 110", "600": "13 106 99", "700": "11 89 83",
            "800": "9 71 66", "900": "6 47 44", "950": "3 24 22",
        },
    },
}

# Never log request bodies: they carry credentials and customer input (ADR-0007).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

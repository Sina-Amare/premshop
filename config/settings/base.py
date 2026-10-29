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
from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from config.queue import queue_name

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEMO_CATALOG = False


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


def site_url(value: str) -> str:
    """SITE_URL as scheme and host, with no trailing slash for a path to follow."""
    parsed = urlparse(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path.strip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise ImproperlyConfigured(
            f"SITE_URL must be a scheme and a host, like https://premshop.ir; got {value!r}"
        )
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


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
# The shop's own address. Canonical links, Open Graph tags and the links in emails
# are built on it, never on the request's Host header, which is whatever the client
# sent. Required in production (prod.py).
SITE_URL = site_url(env("SITE_URL") or "http://127.0.0.1:8000")

INSTALLED_APPS = [
    # unfold must precede django.contrib.admin: it overrides the admin templates,
    # and Django resolves templates in INSTALLED_APPS order. Unfold's own app,
    # with the panel's site in place of Unfold's (apps/core/admin_site.py).
    "apps.core.admin_site.PanelAdminConfig",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # For pg_trgm: the extension operation, the GIN index and the similarity
    # functions the catalog search falls back to.
    "django.contrib.postgres",
    "apps.accounts",
    "apps.catalog",
    "apps.cart",
    "apps.orders",
    "apps.payments",
    "huey.contrib.djhuey",
    "apps.core",
]

MIDDLEWARE = [
    # Outermost, so it compresses whatever the stack below produces. The home
    # page is 27 KB of HTML and 4.9 KB compressed, which on a filtered Iranian
    # mobile link is worth more than any amount of markup trimming.
    #
    # On BREACH, because compressing a response is never free: the attack needs
    # a secret and attacker-controlled text in the SAME response, and then
    # reads the compressed size to confirm guesses. That shape does exist here
    # — /search/?q=… reflects the query into a page whose header carries the
    # logout form's CSRF token. It is defused because Django masks the token
    # with a fresh random salt per request (default since 1.10, hardened in
    # 4.1), so the bytes differ every time and no size correlates with a guess.
    # Nothing else secret reaches a response body: codes and credentials are
    # banned from every rendered surface by ADR-0008, and the session id lives
    # in a cookie, which this middleware does not touch.
    #
    # Revisit the moment a page renders a delivered credential — the order
    # detail page at S5 is the one that will want to. At that point either that
    # view opts out of compression or the credential does not go in the HTML.
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.OrderPagePrivacy",
    # Before everything else that might touch the cache, so its 503 wins over a
    # traceback no matter which view raised.
    "apps.core.middleware.CacheUnavailableMiddleware",
    # Before anything reads the path or the query string.
    "apps.core.middleware.RejectNul",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Before the locale middleware, so the negotiation it runs never sees a
    # browser's Accept-Language unless the operator has actually chosen one,
    # and never sees the panel's choice outside the panel.
    "apps.core.middleware.PersianUnlessChosen",
    # After sessions, because that is where the chosen language is stored, and
    # before common, whose redirects depend on the active locale. Without it
    # the admin's language switch writes the choice and nothing reads it.
    "django.middleware.locale.LocaleMiddleware",
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
                # Shared site facts and catalog navigation.
                "apps.core.context.shell",
                "apps.catalog.context.navigation",
                "apps.cart.context.context",
                # What the shop promises on every page; see apps/catalog/promises.py.
                "apps.catalog.promises.context",
            ],
        },
    },
]

# Django's own CSRF page renders without a request, so without the shop around it.
CSRF_FAILURE_VIEW = "apps.core.errors.csrf_failure"

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": database_from_url(env("DATABASE_URL", required=True))}
DATABASES["default"]["OPTIONS"] = {"options": "-c pg_trgm.word_similarity_threshold=0.3"}

REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")

HUEY = {
    "name": queue_name(DATABASES["default"]),
    "huey_class": "huey.RedisHuey",
    "connection": {"url": REDIS_URL},
    "immediate": False,
    "results": False,
    "consumer": {"workers": 1, "worker_type": "thread"},
}

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

FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")
FIELD_ENCRYPTION_KEY_FALLBACKS = env_list("FIELD_ENCRYPTION_KEY_FALLBACKS")

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
# The shop is Persian; English is here so a non-Persian reader can navigate the
# panel, and it falls back to Django's own admin strings. Leaving this unset
# would offer the operator every locale Django ships.
LANGUAGES = [("fa", "فارسی"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
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
    "SITE_SUBHEADER": _("مدیریت فروشگاه"),
    "SITE_ICON": lambda request: static("icon.svg"),
    "SITE_SYMBOL": "storefront",
    "SITE_URL": "/",
    # The shop's own mark, so the panel's tab is not the browser's blank page
    # icon while the storefront's tab has a logo.
    "SITE_FAVICONS": [{"rel": "icon", "href": lambda request: static("icon.svg")}],
    # Every page, including the dashboard and the login screen. Those two
    # extend admin/base.html and unfold/layouts/unauthenticated.html directly,
    # skipping base_site.html — so a stylesheet hung on base_site's
    # `extrastyle` block reached every page but the first one the operator
    # opens, which is exactly where the font looked unloaded.
    #
    # STYLES loads *before* Unfold's own stylesheet, which does not matter
    # here: Unfold declares --font-sans inside `@layer theme`, and unlayered
    # CSS beats a layer whatever the source order.
    "STYLES": [
        lambda request: static("css/admin.css"),
        lambda request: static("css/admin-experience.css"),
    ],
    # The panel's own behaviour (static/js/admin.js), on every page for the
    # same reason: the icons and the Latin counters are on all of them.
    "SCRIPTS": [
        lambda request: static("js/admin.js"),
        lambda request: static("js/admin-experience.js"),
    ],
    # The first page counts the catalogue and lists what needs the operator.
    "DASHBOARD_CALLBACK": "apps.catalog.dashboard.dashboard_callback",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    # Matches --radius-sm on the storefront, so the two do not look like two
    # products. Unfold's default is rounder than anything this project draws.
    "BORDER_RADIUS": "6px",
    # The switch the owner asked for. It needs LocaleMiddleware and the
    # set_language URL, both wired above and in config/urls.py — with
    # SHOW_LANGUAGES on and either missing, every admin page raises.
    "SHOW_LANGUAGES": True,
    # No LANGUAGE_FLAGS: a flag is a country, not a language, and the menu
    # already names each one in its own script. The repository also bans
    # emoji outright, and the lint caught these.
    # Ctrl+K jumps to a model. With a dozen products it is faster than the
    # sidebar, and it costs nothing to turn on.
    "COMMAND": {"search_models": True, "show_history": True},
    "SIDEBAR": {
        "show_search": True,
        # Deliberately off: it lists every registered model including Django's
        # own plumbing, which is the bare app list this panel is replacing.
        "show_all_applications": False,
        # Translated like the models' names, so the language switch reaches
        # the sidebar; capfirst because those names are lower case in English.
        "navigation": [
            {
                "title": _("فروشگاه"),
                "separator": False,
                "items": [
                    {
                        "title": _("سفارش‌ها"),
                        "icon": "receipt_long",
                        "link": reverse_lazy("admin:orders_order_changelist"),
                    },
                    {
                        "title": _("کدهای تخفیف"),
                        "icon": "local_offer",
                        "link": reverse_lazy("admin:orders_discountcode_changelist"),
                    },
                    {
                        "title": capfirst(_("محصولات")),
                        "icon": "inventory_2",
                        "link": reverse_lazy("admin:catalog_product_changelist"),
                    },
                    {
                        # The one view of every price and margin side by side.
                        "title": capfirst(_("پلن‌ها و قیمت‌ها")),
                        "icon": "sell",
                        "link": reverse_lazy("admin:catalog_plan_changelist"),
                    },
                    {
                        "title": capfirst(_("دسته‌بندی‌ها")),
                        "icon": "category",
                        "link": reverse_lazy("admin:catalog_category_changelist"),
                    },
                ],
            },
            {
                "title": _("مشتریان"),
                "separator": True,
                "items": [
                    {
                        "title": _("کاربران"),
                        "icon": "person",
                        "link": reverse_lazy("admin:accounts_user_changelist"),
                    },
                ],
            },
        ],
    },
    # Cobalt is shared with the storefront. Unfold's own default ramp is violet, and
    # "R G B" triples are converted to rgb() for it (unfold/utils.convert_color).
    "COLORS": {
        "primary": {
            "50": "237 242 255",
            "100": "220 230 253",
            "200": "187 206 249",
            "300": "145 175 241",
            "400": "94 137 230",
            "500": "55 105 221",
            "600": "36 89 211",
            "700": "25 70 176",
            "800": "23 59 142",
            "900": "24 51 111",
            "950": "18 32 65",
        },
    },
}

# Never log request bodies: they carry credentials and customer input (ADR-0007).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"private_links": {"()": "apps.core.observability.PrivateLinkFilter"}},
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["private_links"],
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

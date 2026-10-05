"""
Django settings for the Library Management System.

Every deployment-specific value (secrets, hosts, database, email) comes from
environment variables. See `.env.example` for the full list.
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines) so local development needs no extra tooling.

    Real environment variables always win over values in the file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


if os.environ.get("TESTING") != "1":  # tests must not depend on a developer's .env
    _load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

DEBUG = env_bool("DEBUG", False)
TESTING = env_bool("TESTING", False)

SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "dev-only-insecure-key-change-me"
    else:
        raise ImproperlyConfigured(
            'SECRET_KEY is not set. Generate one with: python -c "import secrets; print(secrets.token_urlsafe(50))"'
        )

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Public base URL used in emails, e.g. https://library.example.org
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Domain
    "apps.core",
    "apps.accounts",
    "apps.members",
    "apps.catalogue",
    "apps.circulation",
    "apps.fees",
    "apps.notifications",
    "apps.inventory",
    # Interfaces
    "apps.public",
    "apps.portal",
    "apps.staff",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestSettingsMiddleware",
    "apps.core.middleware.FirstRunSetupMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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
                "apps.core.context_processors.library",
            ],
            "builtins": [
                "apps.core.templatetags.library",
                "django.templatetags.static",
                "django.contrib.humanize.templatetags.humanize",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database — PostgreSQL in production, SQLite for development / tiny installs
# ---------------------------------------------------------------------------

DEFAULT_DB_URL = f"sqlite:///{BASE_DIR / 'data' / 'library.sqlite3'}"
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", DEFAULT_DB_URL),
        conn_max_age=env_int("DB_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    (BASE_DIR / "data").mkdir(exist_ok=True)
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].update({"timeout": 20})

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication & security
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "portal:dashboard"
LOGOUT_REDIRECT_URL = "public:home"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
if TESTING:
    # Hashing speed only matters for test runtime; production keeps Argon2.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Cookies are secure (HTTPS-only) unless running in DEBUG. Override with COOKIE_SECURE.
COOKIE_SECURE = env_bool("COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_SECURE = COOKIE_SECURE
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 24 * 14)
SESSION_COOKIE_NAME = "library_session"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
if env_bool("BEHIND_PROXY", False):
    # Trust the X-Forwarded-Proto header set by Caddy / nginx.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
TRUST_X_FORWARDED_FOR = env_bool("BEHIND_PROXY", False)

# Optional one-time token required by the first-run setup page (recommended
# for public deployments so nobody else can claim the installation first).
SETUP_TOKEN = env("SETUP_TOKEN")

# Login rate limiting
LOGIN_RATE_LIMIT_PER_ACCOUNT = env_int("LOGIN_RATE_LIMIT_PER_ACCOUNT", 5)
LOGIN_RATE_LIMIT_PER_IP = env_int("LOGIN_RATE_LIMIT_PER_IP", 30)
LOGIN_RATE_LIMIT_WINDOW = env_int("LOGIN_RATE_LIMIT_WINDOW", 15 * 60)

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = env("TIME_ZONE", "Asia/Colombo")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
SERVE_MEDIA = env_bool("SERVE_MEDIA", True)
WHITENOISE_USE_FINDERS = DEBUG or TESTING
WHITENOISE_AUTOREFRESH = DEBUG or TESTING
if DEBUG or TESTING:
    STATIC_ROOT.mkdir(exist_ok=True)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or TESTING
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
MAX_COVER_UPLOAD_BYTES = 5 * 1024 * 1024

# ---------------------------------------------------------------------------
# Email — any SMTP provider (SendGrid, Mailgun, SES, Gmail, Microsoft 365, …)
# ---------------------------------------------------------------------------

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 15)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Library <no-reply@library.local>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_MAX_ATTEMPTS = env_int("EMAIL_MAX_ATTEMPTS", 4)

# ---------------------------------------------------------------------------
# Library features
# ---------------------------------------------------------------------------

# Recommendation strategy — swap for an AI-backed class later without touching views.
RECOMMENDER_CLASS = env("RECOMMENDER_CLASS", "apps.catalogue.recommendations.SimpleRecommender")
# Fill book details from Open Library when an ISBN is scanned.
ISBN_LOOKUP_ENABLED = env_bool("ISBN_LOOKUP_ENABLED", True)
BACKUP_DIR = Path(env("BACKUP_DIR", str(BASE_DIR / "backups")))

# ---------------------------------------------------------------------------
# Logging — technical details go to logs, never to users
# ---------------------------------------------------------------------------

LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
if TESTING:
    LOGGING["root"]["level"] = "CRITICAL"
    LOGGING["loggers"]["apps"]["level"] = "CRITICAL"
    LOGGING["loggers"]["django"]["level"] = "CRITICAL"
    LOGGING["loggers"]["django.security"]["level"] = "CRITICAL"

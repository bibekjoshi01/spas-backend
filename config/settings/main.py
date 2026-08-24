# ruff: noqa
import os
import re
from datetime import timedelta
from pathlib import Path
import sys

from dotenv import load_dotenv
from typing import Any
from django.core.exceptions import ImproperlyConfigured

from .ckeditor_settings import *
from .jazzmin_settings import *
from .logging_config import *

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
APPS_DIR = BASE_DIR / "src"


def _as_bool(value):
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _first_domain_host(hosts):
    for host in hosts:
        cleaned_host = host.strip().lstrip(".")
        if cleaned_host and cleaned_host != "*":
            return cleaned_host
    return ""


DEBUG = _as_bool(os.getenv("DEBUG", "True"))

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-secret"
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG=False.")

ALLOWED_HOSTS = _csv_env("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    if DEBUG:
        ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", ".localhost"]
    else:
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set when DEBUG=False.")

PRIMARY_DOMAIN_SUFFIX = os.getenv("PRIMARY_DOMAIN_SUFFIX", "").strip().lstrip(".")
if not PRIMARY_DOMAIN_SUFFIX:
    PRIMARY_DOMAIN_SUFFIX = _first_domain_host(ALLOWED_HOSTS)
if not PRIMARY_DOMAIN_SUFFIX:
    if DEBUG:
        PRIMARY_DOMAIN_SUFFIX = "localhost"
    else:
        raise ImproperlyConfigured("PRIMARY_DOMAIN_SUFFIX must be set when DEBUG=False.")

CORS_ALLOW_ALL_ORIGINS = _as_bool(os.getenv("CORS_ALLOW_ALL_ORIGINS", "True" if DEBUG else "False"))
CORS_ALLOWED_ORIGINS = _csv_env("CORS_ALLOWED_ORIGINS")
CSRF_TRUSTED_ORIGINS = _csv_env("CSRF_TRUSTED_ORIGINS")

# The frontend is served per college from its own subdomain, so the allowed
# origins cannot be enumerated — one regex covers every tenant of the app
# domain instead of a list that has to be edited on every signup.
_APP_DOMAIN = os.getenv("FRONTEND_DOMAIN", PRIMARY_DOMAIN_SUFFIX).strip().lstrip(".")

CORS_ALLOWED_ORIGIN_REGEXES = []
if _APP_DOMAIN:
    _escaped_domain = re.escape(_APP_DOMAIN)
    CORS_ALLOWED_ORIGIN_REGEXES = [
        rf"^https?://([a-z0-9-]+\.)?{_escaped_domain}(:\d+)?$",
    ]
    # django-tenants routes by Host, and CSRF must trust the same set.
    CSRF_TRUSTED_ORIGINS += [
        f"https://*.{_APP_DOMAIN}",
        f"http://*.{_APP_DOMAIN}",
    ]

CORS_ALLOW_CREDENTIALS = True
if not DEBUG and not CORS_ALLOW_ALL_ORIGINS and not CORS_ALLOWED_ORIGINS:
    raise ImproperlyConfigured(
        "CORS_ALLOWED_ORIGINS must be set when CORS_ALLOW_ALL_ORIGINS=False.",
    )

TESTING = "test" in sys.argv or "PYTEST_VERSION" in os.environ

SHARED_APPS = [
    "jazzmin",
    "corsheaders",
    "django_ckeditor_5",
    "django_tenants",
    "tenants",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "control_plane",
]

if DEBUG and not TESTING:
    SHARED_APPS += ["debug_toolbar"]


TENANT_APPS = (
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "django.contrib.staticfiles",
    "rest_framework_simplejwt.token_blacklist",
    "django_celery_results",
    "django_celery_beat",
    "simple_history",
    "src.user",
    "src.academics",
    "src.students",
    "src.performance",
)

INSTALLED_APPS = list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django_tenants.middleware.TenantMainMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "src.libs.middleware.RequestContextMiddleware",
    "src.libs.middleware.NoIndexMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "control_plane.middleware.PlatformUserJWTMiddleware",
    "src.libs.middleware.TenantStatusMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    MIDDLEWARE.insert(2, "src.libs.middleware.BlockPostmanMiddleware")

if DEBUG and not TESTING:
    # Ensure debug toolbar middleware is active when running in DEBUG (and not testing)
    MIDDLEWARE.insert(2, "debug_toolbar.middleware.DebugToolbarMiddleware")

INTERNAL_IPS = ["127.0.0.1"]

ROOT_URLCONF = "config.tenant_urls"
PUBLIC_SCHEMA_URLCONF = "config.platform_urls"
ADMIN_URL = "cms/"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES: dict[str, dict[str, Any]] = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

TENANT_MODEL = "tenants.Tenant"
TENANT_DOMAIN_MODEL = "tenants.Domain"
DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",)

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kathmandu"
USE_I18N = True
USE_TZ = True

SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None

# SECURITY
# ------------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# AUTHENTICATION
# ------------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "/accounts/login/"

AUTH_USER_MODEL = "user.User"

# PASSWORDS
# ------------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# FIXTURES
# ------------------------------------------------------------------------------
FIXTURE_DIRS = (str(APPS_DIR / "fixtures"),)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# STATIC
# ------------------------------------------------------------------------------
STATIC_ROOT = str(BASE_DIR / "staticfiles")
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# REST FRAMEWORK
# ------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 10,
    "DEFAULT_RENDERER_CLASSES": (
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
        "djangorestframework_camel_case.render.CamelCaseBrowsableAPIRenderer",
    ),
    "DEFAULT_PARSER_CLASSES": [
        "src.libs.parser.CustomNestedParser",
        "djangorestframework_camel_case.parser.CamelCaseFormParser",
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
    ],
    "JSON_UNDERSCOREIZE": {
        "no_underscore_before_number": True,
    },
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "src.libs.handler.custom_exception_handler",
}


NESTED_FORM_PARSER = {"OPTIONS": {"allow_empty": True, "allow_blank": True}}
APPEND_SLASH = False
CORS_URLS_REGEX = r"^/api/.*$"

SPECTACULAR_SETTINGS = {
    "SCHEMA_COMPONENT_SPLIT_UNDERSCORES": False,
    "TITLE": "Operon Backend API",
    "DESCRIPTION": "Documentation of API endpoints of OPERON Backend",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "drf_spectacular.contrib.djangorestframework_camel_case.camelize_serializer_fields",
    ],
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SCHEMA_PATH_PREFIX": "/api/v1/internal",
    "SWAGGER_UI_SETTINGS": {
        "defaultModelsExpandDepth": -1,
    },
}


SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=int(os.getenv("ACCESS_TOKEN_TIME", default=60)),
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=int(os.getenv("REFRESH_TOKEN_TIME", default=10)),
    ),
    "ROTATE_REFRESH_TOKEN": False,
    "BLACKLIST_AFTER_ROTATION": False,
}

# Constants
# -------------------------------------------------------------------------------
IMAGE_MAX_UPLOAD_SIZE = int(os.getenv("IMAGE_MAX_UPLOAD_SIZE", default=6291456))

DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB

# Celery, Redis, Cache
# ------------------------------------------------------------------------------
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_DB_BROKER = 0
REDIS_DB_CACHE = 1
REDIS_DB_RESULTS = 2

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB_CACHE}",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
        "TIMEOUT": 300,  # default TTL
    },
    "debug-toolbar": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
}

if TESTING:
    # Tests must not depend on a running Redis, and each one starts with an
    # empty throttle bucket rather than inheriting the previous test's.
    CACHES["default"] = {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }

CELERY_BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB_BROKER}"
CELERY_RESULT_BACKEND = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB_RESULTS}"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_ENABLE_UTC = True
CELERY_TIMEZONE = TIME_ZONE

CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

CELERY_TASK_IGNORE_RESULT = False
CELERY_TASK_TRACK_STARTED = True
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_RESULT_EXTENDED = True

CELERY_RESULT_EXPIRES = 3600

# Beat settings for periodic tasks
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CELERY_TASK_DEFAULT_QUEUE = "default"

CELERY_TASK_QUEUES: dict[str, dict[str, Any]] = {
    "default": {},
    "critical": {},
    "email": {},
    "low": {},
}


DEBUG_TOOLBAR_CONFIG = {
    "TOOLBAR_STORE_CLASS": "debug_toolbar.store.CacheStore",
    "CACHE_BACKEND": "debug-toolbar",
}

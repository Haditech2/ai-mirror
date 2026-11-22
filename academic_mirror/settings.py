from __future__ import annotations

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "development-only-change-this-django-secret-before-production",
)
DEBUG = env_bool("DEBUG", not env_bool("VERCEL"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
ALLOWED_HOSTS = [item.strip() for item in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,*").split(",") if item.strip()]
CSRF_TRUSTED_ORIGINS = [
    item.strip()
    for item in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if item.strip()
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "mirror.apps.MirrorConfig",
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
    "mirror.middleware.SecurityHeadersMiddleware",
    "mirror.middleware.RetentionMiddleware",
]

ROOT_URLCONF = "academic_mirror.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "mirror.context_processors.application_settings",
            ],
        },
    }
]
WSGI_APPLICATION = "academic_mirror.wsgi.application"
ASGI_APPLICATION = "academic_mirror.asgi.application"

database_path = Path(os.getenv("DATABASE_PATH", "db.sqlite3"))
if not database_path.is_absolute():
    database_path = BASE_DIR / database_path
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": database_path,
        "OPTIONS": {"timeout": 20},
    }
}
database_url = os.getenv("DATABASE_URL", "").strip()
if database_url:
    DATABASES["default"] = dj_database_url.parse(
        database_url, conn_max_age=0, disable_server_side_cursors=True
    )

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files are served directly by Django during local development. A web
# server may map this URL to collectstatic output in production.
STATIC_URL = "/assets/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

PRIVATE_STORAGE_ROOT = Path(os.getenv("PRIVATE_STORAGE_ROOT", "storage"))
if not PRIVATE_STORAGE_ROOT.is_absolute():
    PRIVATE_STORAGE_ROOT = BASE_DIR / PRIVATE_STORAGE_ROOT

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "console:login"
LOGIN_REDIRECT_URL = "console:dashboard"
LOGOUT_REDIRECT_URL = "console:login"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", ENVIRONMENT == "production")
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", ENVIRONMENT == "production")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
SETTINGS_ENCRYPTION_KEY = os.getenv("SETTINGS_ENCRYPTION_KEY", "")
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 12)
MAX_IMAGE_PIXELS = env_int("MAX_IMAGE_PIXELS", 24_000_000)
GUEST_SESSION_MINUTES = env_int("GUEST_SESSION_MINUTES", 30)
DOWNLOAD_TOKEN_MINUTES = env_int("DOWNLOAD_TOKEN_MINUTES", 1440)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "eventadmin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me-before-production")

AI_PROVIDER = os.getenv("AI_PROVIDER", "disabled")
AI_API_KEY = os.getenv("AI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
REPLICATE_API_BASE_URL = os.getenv("REPLICATE_API_BASE_URL", "https://api.replicate.com/v1")
REPLICATE_MODEL_OWNER = os.getenv("REPLICATE_MODEL_OWNER", "cuuupid")
REPLICATE_MODEL_NAME = os.getenv("REPLICATE_MODEL_NAME", "idm-vton")
REPLICATE_MODEL_VERSION = os.getenv("REPLICATE_MODEL_VERSION", "")
FASHN_API_BASE_URL = os.getenv("FASHN_API_BASE_URL", "https://api.fashn.ai/v1")
FASHN_MODEL_NAME = os.getenv("FASHN_MODEL_NAME", "tryon-v1.6")
GENERATION_WORKERS = max(1, env_int("GENERATION_WORKERS", 2))
GENERATION_TIMEOUT_SECONDS = env_int("GENERATION_TIMEOUT_SECONDS", 150)
GENERATION_MAX_RETRIES = max(0, env_int("GENERATION_MAX_RETRIES", 2))
GENERATION_INLINE = env_bool("GENERATION_INLINE", False)

FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = (MAX_UPLOAD_MB + 1) * 1024 * 1024

if ENVIRONMENT == "production":
    unsafe = []
    if SECRET_KEY.startswith("development-only"):
        unsafe.append("SECRET_KEY")
    if ADMIN_PASSWORD == "change-me-before-production":
        unsafe.append("ADMIN_PASSWORD")
    if unsafe:
        raise RuntimeError(f"Unsafe production configuration: replace {', '.join(unsafe)}.")

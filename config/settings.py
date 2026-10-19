"""
Django settings for config project.

Настройки читаются из окружения (.env), чтобы секреты не попадали в git.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# --- Безопасность -----------------------------------------------------------

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-change-me-in-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if h.strip()
]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "documents",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # Таймаут, чтобы веб-процесс и воркер (этап 10) не конфликтовали
        "OPTIONS": {"timeout": 10},
    }
}


# Валидаторы паролей.
#
# CommonPasswordValidator и UserAttributeSimilarityValidator отключены
# осознанно: проект демонстрационный, и пароли вида demo12345 должны
# проходить регистрацию (иначе на защите не создать второй аккаунт).
# Для реальной эксплуатации эти два валидатора нужно вернуть.
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization

LANGUAGE_CODE = "ru-ru"

TIME_ZONE = "Europe/Moscow"

USE_I18N = True

USE_TZ = True


# Static files

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]


# Media files (загруженные пользователем)

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# Default primary key field type

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Аутентификация (этап 8) ------------------------------------------------

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "documents:index"
LOGOUT_REDIRECT_URL = "login"

# Регистрация открыта: для демонстрации нужны два пользователя,
# чтобы показать изоляцию документов.
REGISTRATION_ENABLED = os.environ.get("REGISTRATION_ENABLED", "True") == "True"

# Ограничение на количество документов пользователя.
# 0 — без ограничения (значение по умолчанию для демонстрации).
MAX_DOCS_PER_USER = int(os.environ.get("MAX_DOCS_PER_USER", "0"))


# --- GigaChat ---------------------------------------------------------------

GIGACHAT_MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Max")

# Логирование времени и токенов на обработку документа
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "documents": {
            "handlers": ["console"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
        },
    },
}

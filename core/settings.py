

import os
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")

# Piloté par env var pour permettre un déploiement en production
# (Render...) sans toucher au code : par défaut True pour ne rien
# casser en développement local si la variable n'est pas définie.
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() in ("true", "1", "t", "yes")

# Idem : '*' par défaut (développement), restreint via env var en
# production (ex: "carte-asso-api.onrender.com,.onrender.com").
_hosts_env = os.getenv("DJANGO_ALLOWED_HOSTS")
ALLOWED_HOSTS = _hosts_env.split(",") if _hosts_env else ['*']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'adhesion',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # Sert les fichiers statiques (admin Django, etc.) directement
    # depuis le process Django en production, sans serveur séparé -
    # inoffensif en développement (STATIC_ROOT vide, DEBUG sert déjà
    # les statiques autrement).
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases
#
# SQLite en développement local (comme avant) ; si DATABASE_URL est
# défini (Neon/Postgres en production), il prend le dessus - c'est
# la seule chose à changer pour basculer d'un environnement à l'autre.
import dj_database_url

DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
# Cible de `collectstatic` en production (whitenoise sert depuis ici).
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Fichiers médias (photos membres/participants, logo association)
# ATTENTION : sur Render (plan gratuit, sans disque persistant), ce
# dossier est réinitialisé à chaque redéploiement - les photos
# téléversées ne survivent pas. À migrer vers un stockage externe
# (ex: Cloudinary, S3) si ça devient gênant en usage réel.
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'



# Origines autorisées à appeler l'API. Par défaut (développement),
# tout est autorisé comme avant. En production, définir
# DJANGO_CORS_ALLOWED_ORIGINS (URLs séparées par des virgules, ex:
# "https://ap2a-portail.vercel.app") pour restreindre - le mobile
# n'est pas concerné par CORS (ce n'est pas un navigateur).
_cors_env = os.getenv("DJANGO_CORS_ALLOWED_ORIGINS")
if _cors_env:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    CORS_ALLOW_ALL_ORIGINS = True

CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'ngrok-skip-browser-warning',  # 👈 Indispensable pour Ngrok
]

# ---------------------------------------------------------------------
# Trousseau de clés HMAC versionné (signature des cartes QR/NFC)
# ---------------------------------------------------------------------
HMAC_CLES = {
    1: os.getenv("HMAC_CLE_V1"),
    # 2: os.getenv("HMAC_CLE_V2"),   # à décommenter le jour d'une rotation
}

HMAC_VERSION_ACTIVE = int(os.getenv("HMAC_VERSION_ACTIVE", 1))

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "adhesion.authentication.AuthentificationParJeton",
    ],
}

# ---------------------------------------------------------------------
# Configuration Email (SMTP & Console fallback)
# ---------------------------------------------------------------------
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("true", "1", "t", "yes")
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", os.getenv("EMAIL_HOST_USER") or "AP2A <contact@ap2a.org>")
# Sans timeout, une connexion SMTP qui traîne (réseau lent, port bloqué)
# peut bloquer indéfiniment la requête HTTP en cours - Django n'impose
# aucune limite par défaut.
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "15"))

if not EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
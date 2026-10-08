

import os
import warnings
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/



def _env_bool(nom: str, defaut: bool) -> bool:
    valeur = os.getenv(nom)
    if valeur is None or valeur == "":
        return defaut
    return valeur.lower() in ("true", "1", "t", "yes")


# Sûr par défaut : une variable oubliée en production doit FERMER
# l'application, jamais l'ouvrir. Le développement local active
# explicitement DJANGO_DEBUG=True dans son .env (voir .env.example).
DEBUG = _env_bool("DJANGO_DEBUG", False)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY doit être définie en production.")
    SECRET_KEY = "dev-uniquement-cle-non-secrete"

# En production, aucun hôte par défaut : DJANGO_ALLOWED_HOSTS doit être
# défini (ex: ".onrender.com"), sinon Django refuse toutes les requêtes.
_hosts_env = os.getenv("DJANGO_ALLOWED_HOSTS")
if _hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _hosts_env.split(",") if h.strip()]
else:
    ALLOWED_HOSTS = ["*"] if DEBUG else []


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



# Origines autorisées à appeler l'API : DJANGO_CORS_ALLOWED_ORIGINS
# (URLs séparées par des virgules, ex: "https://ap2a-portail.vercel.app").
# Sans cette variable, tout est autorisé en développement seulement ;
# en production aucune origine navigateur n'est autorisée. Le mobile
# n'est pas concerné par CORS (ce n'est pas un navigateur).
_cors_env = os.getenv("DJANGO_CORS_ALLOWED_ORIGINS")
if _cors_env:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    CORS_ALLOW_ALL_ORIGINS = DEBUG

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

HMAC_CLES = {version: cle for version, cle in HMAC_CLES.items() if cle}

HMAC_VERSION_ACTIVE = int(os.getenv("HMAC_VERSION_ACTIVE", 1))

# Échouer AU DÉMARRAGE plutôt qu'au premier scan si la clé active
# manque : sinon la première création de carte plante en production.
if HMAC_VERSION_ACTIVE not in HMAC_CLES:
    if not DEBUG:
        raise ImproperlyConfigured(
            f"HMAC_CLE_V{HMAC_VERSION_ACTIVE} doit être définie (clé HMAC active des cartes)."
        )
    HMAC_CLES[HMAC_VERSION_ACTIVE] = "dev-uniquement-cle-hmac-non-secrete"

for _version, _cle in HMAC_CLES.items():
    # HMAC-SHA256 : une clé de moins de 32 octets offre moins de
    # 256 bits de sécurité. Générer avec : python -c "import secrets; print(secrets.token_hex(32))"
    if len(_cle.encode("utf-8")) < 32 and not DEBUG:
        warnings.warn(f"HMAC_CLE_V{_version} fait moins de 32 octets : utiliser une clé plus longue.")

# Transition vers les signatures avec séparation de domaine (voir
# adhesion/utils.py) : accepter encore les signatures sans étiquette
# des cartes NFC et badges émis avant ce changement. À passer à False
# une fois tous les supports physiques réémis.
HMAC_ACCEPTER_SIGNATURES_HERITEES = _env_bool("HMAC_ACCEPTER_SIGNATURES_HERITEES", True)

# ---------------------------------------------------------------------
# Sessions applicatives, tickets de scan, connexion
# ---------------------------------------------------------------------
# Durée de vie absolue d'un jeton de connexion : au-delà, il faut se
# reconnecter (un jeton volé ne reste pas utilisable indéfiniment).
JETON_DUREE_VIE_HEURES = int(os.getenv("JETON_DUREE_VIE_HEURES", 24 * 7))

# Durée de validité d'un ticket de scan (vérification -> confirmation
# de présence) et d'un lien de téléchargement signé.
TICKET_SCAN_DUREE_SECONDES = int(os.getenv("TICKET_SCAN_DUREE_SECONDES", 120))
LIEN_TELECHARGEMENT_DUREE_SECONDES = int(os.getenv("LIEN_TELECHARGEMENT_DUREE_SECONDES", 60))

# Exiger un ticket de scan pour confirmer une présence. Ne désactiver
# que TEMPORAIREMENT, le temps de mettre à jour un client (application
# mobile) qui n'envoie pas encore de ticket.
TICKET_SCAN_OBLIGATOIRE = _env_bool("TICKET_SCAN_OBLIGATOIRE", True)

# Limitation des tentatives de connexion (fenêtre glissante).
CONNEXION_FENETRE_MINUTES = int(os.getenv("CONNEXION_FENETRE_MINUTES", 15))
CONNEXION_MAX_ECHECS_IDENTIFIANT = int(os.getenv("CONNEXION_MAX_ECHECS_IDENTIFIANT", 5))
CONNEXION_MAX_ECHECS_IP = int(os.getenv("CONNEXION_MAX_ECHECS_IP", 50))

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "adhesion.authentication.AuthentificationParJeton",
    ],
    # Défense en profondeur : une vue qui oublierait son
    # @permission_classes n'est PAS publique par défaut.
    "DEFAULT_PERMISSION_CLASSES": [
        "adhesion.permissions.EstAuthentifie",
    ],
    # ?format= est utilisé par l'export de rapports (excel|pdf) : DRF ne
    # doit pas l'interpréter comme un choix de renderer (sinon 404).
    "URL_FORMAT_OVERRIDE": None,
    "EXCEPTION_HANDLER": "adhesion.exceptions.gestionnaire_exceptions",
}

# ---------------------------------------------------------------------
# Durcissement HTTP (production uniquement)
# ---------------------------------------------------------------------
# Taille max d'un corps de requête hors fichiers (JSON...) et seuil au-delà
# duquel un fichier téléversé est écrit sur disque plutôt qu'en mémoire.
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    # Render termine TLS sur son proxy et transmet le schéma d'origine
    # dans X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", 60 * 60 * 24 * 365))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

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

# Sans ceci, les erreurs des envois d'email en arrière-plan (voir
# envoyer_email_arriere_plan) ne sont pas garanties de remonter dans
# les logs du serveur - indispensable pour diagnostiquer un échec de
# livraison en production, où on ne peut pas lire l'exception dans la
# réponse HTTP (l'envoi est déjà terminé quand la réponse part).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

if not EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")

"""
adhesion/utils.py

Génération et vérification de la signature HMAC apposée sur chaque
carte (QR ou NFC), avec support de la rotation de clé versionnée.

Rappel du principe (voir discussion) :
- Le contenu encodé sur la carte est : uuid + version + signature
- La signature prouve que le serveur (et lui seul, car il détient
  la clé secrète) a bien émis cette carte pour cet UUID précis.
- La signature n'est JAMAIS stockée en base : elle est recalculée
  à la volée à chaque scan.
"""

import hmac
import hashlib

from django.conf import settings


def generer_signature(uuid_carte: str) -> tuple[str, int]:
    """
    Calcule la signature HMAC pour un UUID de carte, en utilisant
    la clé actuellement active (HMAC_VERSION_ACTIVE dans settings).

    Appelée UNE SEULE FOIS, au moment de la création d'une carte
    (inscription, ou réémission après perte).

    Retourne un tuple (signature_hexadecimale, numero_version),
    les deux à encoder ensemble sur le QR/la puce.
    """
    version = settings.HMAC_VERSION_ACTIVE
    cle_secrete = settings.HMAC_CLES[version]

    signature = hmac.new(
        key=cle_secrete.encode("utf-8"),
        msg=uuid_carte.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return signature, version


def verifier_signature(uuid_carte: str, signature_recue: str, version_recue: int) -> bool:
    """
    Vérifie qu'une signature reçue lors d'un scan correspond bien
    à l'UUID annoncé, pour la version de clé indiquée.

    Appelée À CHAQUE scan, avant toute recherche en base de données.

    Retourne True si la signature est valide, False sinon.
    """
    # La version reçue doit correspondre à une clé que le serveur
    # connaît encore. Si la clé a été retirée du trousseau (ancienne
    # version révoquée), on rejette sans planter.
    cle_secrete = settings.HMAC_CLES.get(version_recue)
    if cle_secrete is None:
        return False

    signature_attendue = hmac.new(
        key=cle_secrete.encode("utf-8"),
        msg=uuid_carte.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Comparaison en temps constant : ne JAMAIS utiliser "==" ici.
    # Une comparaison naïve s'arrête au premier caractère différent,
    # ce qui fait varier le temps de réponse selon combien de
    # caractères sont corrects - une faille exploitable par mesure
    # de timing pour deviner la signature progressivement.
    return hmac.compare_digest(signature_attendue, signature_recue)


def construire_contenu_carte(uuid_carte: str) -> str:
    """
    Construit la chaîne complète à encoder sur le QR/la puce NFC :
    uuid.version.signature

    Le point "." sert de séparateur simple pour pouvoir décomposer
    la chaîne facilement côté application Flutter au moment du scan.
    """
    signature, version = generer_signature(uuid_carte)
    return f"{uuid_carte}.{version}.{signature}"


def decomposer_contenu_carte(contenu_scanne):
    """
    Fait l'opération inverse : à partir de ce que Flutter a lu sur
    le QR/la puce, sépare l'UUID, la version et la signature.

    Retourne None si le format est invalide (carte corrompue,
    QR d'un autre système, etc.) plutôt que de planter.
    """
    parties = contenu_scanne.split(".")
    if len(parties) != 3:
        return None

    uuid_carte, version_str, signature = parties
    try:
        version = int(version_str)
    except ValueError:
        return None

    return uuid_carte, signature, version
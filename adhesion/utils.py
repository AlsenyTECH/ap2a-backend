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
import time

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


def reconstruire_contenu_carte(uuid_carte: str, version: int) -> str:
    """
    Reconstruit le contenu d'une carte DÉJÀ EXISTANTE, en utilisant la
    version de clé qui a servi à la signer à l'origine (Carte.id_version_cle),
    pas forcément la version active actuelle.

    Nécessaire par exemple pour réafficher le QR d'un membre dans son
    espace virtuel après une rotation de clé : si sa carte a été signée
    en version 1 mais que le serveur signe désormais en version 2, il
    faut continuer à afficher le contenu signé en version 1, sinon la
    signature affichée ne correspondrait plus à ce qui a été imprimé.
    """
    cle_secrete = settings.HMAC_CLES[version]
    signature = hmac.new(
        key=cle_secrete.encode("utf-8"),
        msg=uuid_carte.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
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


# =====================================================================
# QR ROTATIF (carte virtuelle uniquement) : le contenu change toutes
# les 10 secondes, contrairement au QR statique imprimé sur une carte
# physique. Principe proche d'un code TOTP (type Google Authenticator) :
# on signe "uuid:fenêtre_de_temps" au lieu de juste "uuid".
# =====================================================================

DUREE_FENETRE_SECONDES = 10


def fenetre_temps_actuelle() -> int:
    """
    Découpe le temps en blocs de 10 secondes depuis le 1er janvier
    1970 (epoch Unix). Deux appareils qui demandent l'heure à moins
    de 10s d'intervalle tombent dans la même fenêtre, donc calculent
    la même signature - c'est ce qui permet au serveur de vérifier
    sans avoir à se souvenir de quoi que ce soit.
    """
    return int(time.time() // DUREE_FENETRE_SECONDES)


def generer_signature_temporelle(uuid_carte: str, fenetre: int) -> tuple[str, int]:
    """
    Comme generer_signature(), mais le message signé inclut la
    fenêtre de temps : HMAC(uuid:fenetre, cle), pas juste HMAC(uuid, cle).
    Résultat : la signature change à chaque nouvelle fenêtre, même
    pour un UUID identique.
    """
    version = settings.HMAC_VERSION_ACTIVE
    cle_secrete = settings.HMAC_CLES[version]
    message = f"{uuid_carte}:{fenetre}"
    signature = hmac.new(
        key=cle_secrete.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return signature, version


def construire_contenu_rotatif(uuid_carte: str) -> str:
    """
    Contenu à afficher DANS L'APPLI (jamais imprimé) :
    uuid.version.fenetre.signature
    Un quatrième segment (fenetre) par rapport au format statique -
    c'est ce qui permet au scanner de distinguer les deux formats
    (3 segments = carte physique statique, 4 = carte virtuelle rotative).
    """
    fenetre = fenetre_temps_actuelle()
    signature, version = generer_signature_temporelle(uuid_carte, fenetre)
    return f"{uuid_carte}.{version}.{fenetre}.{signature}"


def verifier_signature_temporelle(
    uuid_carte: str, fenetre_recue: int, version_recue: int, signature_recue: str
) -> bool:
    """
    Vérifie une signature rotative, avec une tolérance d'UNE fenêtre
    dans le passé (soit ~20 secondes de validité totale) pour absorber
    le délai entre l'affichage du QR et le moment où le contrôleur
    termine son scan - sans cette tolérance, un QR pourrait expirer
    entre l'ouverture de l'appareil photo et la détection du code.
    """
    fenetre_actuelle = fenetre_temps_actuelle()
    fenetres_acceptees = {fenetre_actuelle, fenetre_actuelle - 1}

    if fenetre_recue not in fenetres_acceptees:
        return False

    cle_secrete = settings.HMAC_CLES.get(version_recue)
    if cle_secrete is None:
        return False

    message = f"{uuid_carte}:{fenetre_recue}"
    signature_attendue = hmac.new(
        key=cle_secrete.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(signature_attendue, signature_recue)


# =====================================================================
# EMAIL PLACEHOLDER (membres créés sans adresse email réelle)
# =====================================================================

SUFFIXE_EMAIL_PLACEHOLDER = "@membre.ap2a.local"


def est_email_reel(email: str | None) -> bool:
    """
    Un membre créé par un admin sans adresse email reçoit un identifiant
    technique <slug>@membre.ap2a.local (voir vue_creer_membre_admin) qui
    ne correspond à aucune boîte mail réelle - il ne faut jamais tenter
    d'y envoyer quoi que ce soit.
    """
    return bool(email) and not email.endswith(SUFFIXE_EMAIL_PLACEHOLDER)


# =====================================================================
# CYCLE DE VIE DES COHORTES : transitions de statut automatiques,
# paresseuses (calculées à la lecture/écriture, pas de tâche planifiée
# type Celery/cron), et verrouillage une fois le cycle terminé.
#
# BROUILLON (planification) -> PROGRAMMEE (manuel, bouton "Lancer la
# formation" = vue_acter_programme_cohorte, notifie les membres ciblés)
# -> EN_COURS (automatique, dès que date_debut est atteinte) -> TERMINEE
# (automatique dès que date_fin est dépassée, OU manuel via le bouton
# "Terminer" = vue_terminer_cohorte pendant EN_COURS). ANNULEE est un
# état terminal atteignable depuis BROUILLON ou PROGRAMMEE seulement
# (annuler une cohorte déjà commencée n'a pas de sens).
# =====================================================================

STATUTS_COHORTE_VERROUILLES = ("TERMINEE", "ANNULEE")

TRANSITIONS_COHORTE_AUTORISEES: dict[str, set[str]] = {
    "BROUILLON": {"PROGRAMMEE", "ANNULEE"},
    "PROGRAMMEE": {"EN_COURS", "ANNULEE"},
    "EN_COURS": {"TERMINEE"},
    "TERMINEE": set(),
    "ANNULEE": set(),
}


def synchroniser_statuts_cohortes() -> None:
    """
    Fait avancer automatiquement le statut de TOUTES les cohortes dont la
    date programmée est atteinte/dépassée - à appeler en tête de toute vue
    qui lit ou modifie des cohortes, pour que le statut affiché soit
    toujours à jour sans dépendre d'une tâche planifiée. Deux UPDATE en
    lot (pas un SELECT+SAVE par cohorte), donc appelable sans souci à
    chaque requête vu le volume de l'application.

    BROUILLON n'avance jamais tout seul (il faut le bouton "Lancer la
    formation" pour notifier les membres au bon moment) - seules les
    transitions PROGRAMMEE -> EN_COURS -> TERMINEE sont automatiques.
    """
    from django.utils import timezone
    from .models import Cohorte

    aujourdhui = timezone.localdate()
    Cohorte.objects.filter(statut="PROGRAMMEE", date_debut__lte=aujourdhui).update(statut="EN_COURS")
    Cohorte.objects.filter(statut__in=["PROGRAMMEE", "EN_COURS"], date_fin__lt=aujourdhui).update(statut="TERMINEE")


def erreur_si_cohorte_verrouillee(cohorte):
    """
    Retourne une Response 400 si la cohorte est terminée ou annulée (plus
    aucune modification possible), sinon None. À appeler après
    synchroniser_statuts_cohortes() dans toute vue qui modifie une cohorte
    ou son contenu (participants, séances, kits...).
    """
    from rest_framework.response import Response
    from rest_framework import status as drf_status

    if cohorte.statut in STATUTS_COHORTE_VERROUILLES:
        libelle = "terminée" if cohorte.statut == "TERMINEE" else "annulée"
        return Response(
            {"erreur": f"Cette cohorte est {libelle}, aucune modification n'est plus possible."},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )
    return None
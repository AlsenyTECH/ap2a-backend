"""
adhesion/utils.py

Génération et vérification de la signature HMAC apposée sur chaque
carte (QR ou NFC) et chaque badge de participant, avec support de la
rotation de clé versionnée.

Rappel du principe :
- Le contenu encodé sur la carte est : uuid + version + signature
- La signature prouve que le serveur (et lui seul, car il détient
  la clé secrète) a bien émis cette carte pour cet UUID précis.
- La signature n'est JAMAIS stockée en base : elle est recalculée
  à la volée à chaque scan.

Séparation de domaine : la même clé K signe trois types d'objets
différents (carte de membre statique, QR rotatif, badge de
participant). Pour qu'une signature valide dans un contexte ne puisse
JAMAIS être rejouée dans un autre, chaque message signé commence par
une étiquette de domaine fixe :

    HMAC(K, "AP2A|carte|<uuid>")
    HMAC(K, "AP2A|badge|<uuid>")
    HMAC(K, "AP2A|rotatif|<uuid>|<fenetre>")

Le séparateur "|" n'apparaît jamais dans un UUID canonique ni dans un
entier, donc l'encodage est injectif : deux messages de domaines
différents ne peuvent pas être égaux.

Compatibilité : les cartes NFC et badges imprimés AVANT l'ajout des
étiquettes ont été signés sur l'UUID nu - HMAC(K, "<uuid>"). Tant que
HMAC_ACCEPTER_SIGNATURES_HERITEES est vrai, la vérification accepte
encore ce format (et le journalise) pour les supports STATIQUES
uniquement. Une fois tous les supports réémis, passer ce réglage à
False supprime définitivement le format sans domaine.
"""

import hmac
import hashlib
import logging
import secrets
import time
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

DOMAINE_CARTE = "AP2A|carte"
DOMAINE_BADGE = "AP2A|badge"
DOMAINE_ROTATIF = "AP2A|rotatif"

DOMAINES_STATIQUES = (DOMAINE_CARTE, DOMAINE_BADGE)


def _cle_pour_version(version: int) -> bytes | None:
    """Clé du trousseau pour cette version, ou None si inconnue/révoquée."""
    cle = settings.HMAC_CLES.get(version)
    return cle.encode("utf-8") if cle else None


def _hmac_hex(cle: bytes, message: str) -> str:
    return hmac.new(cle, message.encode("utf-8"), hashlib.sha256).hexdigest()


def _message_statique(domaine: str, uuid_carte: str) -> str:
    if domaine not in DOMAINES_STATIQUES:
        raise ValueError(f"Domaine de signature statique inconnu : {domaine}")
    return f"{domaine}|{uuid_carte}"


def generer_signature(uuid_carte: str, domaine: str = DOMAINE_CARTE) -> tuple[str, int]:
    """
    Calcule la signature HMAC d'un support statique (carte de membre
    ou badge de participant), avec la clé active (HMAC_VERSION_ACTIVE).

    Retourne un tuple (signature_hexadecimale, numero_version),
    les deux à encoder ensemble sur le QR/la puce.
    """
    version = settings.HMAC_VERSION_ACTIVE
    signature = _hmac_hex(_cle_pour_version(version), _message_statique(domaine, uuid_carte))
    return signature, version


def verifier_signature(
    uuid_carte: str, signature_recue: str, version_recue: int, domaine: str = DOMAINE_CARTE
) -> bool:
    """
    Vérifie la signature d'un support statique pour le domaine attendu
    (carte de membre ou badge). Appelée À CHAQUE scan, avant toute
    recherche en base de données.
    """
    # La version reçue doit correspondre à une clé que le serveur
    # connaît encore. Si la clé a été retirée du trousseau (ancienne
    # version révoquée), on rejette sans planter.
    cle = _cle_pour_version(version_recue)
    if cle is None:
        return False

    # Comparaison en temps constant : ne JAMAIS utiliser "==" ici.
    # Une comparaison naïve s'arrête au premier caractère différent,
    # ce qui fait varier le temps de réponse selon combien de
    # caractères sont corrects - une faille exploitable par mesure
    # de timing pour deviner la signature progressivement.
    attendue = _hmac_hex(cle, _message_statique(domaine, uuid_carte))
    if hmac.compare_digest(attendue, signature_recue):
        return True

    if settings.HMAC_ACCEPTER_SIGNATURES_HERITEES:
        heritee = _hmac_hex(cle, uuid_carte)
        if hmac.compare_digest(heritee, signature_recue):
            logger.info("Signature héritée (sans domaine) acceptée pour %s %s", domaine, uuid_carte)
            return True

    return False


def construire_contenu_carte(uuid_carte: str, domaine: str = DOMAINE_CARTE) -> str:
    """
    Construit la chaîne complète à encoder sur le QR/la puce NFC :
    uuid.version.signature

    Le point "." sert de séparateur simple pour pouvoir décomposer
    la chaîne facilement côté application au moment du scan.
    """
    signature, version = generer_signature(uuid_carte, domaine)
    return f"{uuid_carte}.{version}.{signature}"


def decomposer_contenu_carte(contenu_scanne):
    """
    Fait l'opération inverse : à partir de ce qui a été lu sur le
    QR/la puce, sépare l'UUID, la version et la signature.

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
# les 10 secondes, contrairement au contenu statique d'une carte
# physique. Principe proche d'un code TOTP (type Google Authenticator) :
# on signe "domaine|uuid|fenêtre_de_temps" au lieu de "domaine|uuid".
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


def _message_rotatif(uuid_carte: str, fenetre: int) -> str:
    return f"{DOMAINE_ROTATIF}|{uuid_carte}|{fenetre}"


def generer_signature_temporelle(uuid_carte: str, fenetre: int) -> tuple[str, int]:
    """
    Comme generer_signature(), mais le message signé inclut la
    fenêtre de temps : la signature change à chaque nouvelle fenêtre,
    même pour un UUID identique.
    """
    version = settings.HMAC_VERSION_ACTIVE
    signature = _hmac_hex(_cle_pour_version(version), _message_rotatif(uuid_carte, fenetre))
    return signature, version


def construire_contenu_rotatif(uuid_carte: str) -> str:
    """
    Contenu à afficher DANS L'APPLI (jamais imprimé) :
    uuid.version.fenetre.signature
    Un quatrième segment (fenetre) par rapport au format statique -
    c'est ce qui permet au scanner de distinguer les deux formats
    (3 segments = support statique, 4 = carte virtuelle rotative).
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
    termine son scan.

    Le rejeu d'un QR dans sa fenêtre de validité n'est pas bloqué ICI
    (la vérification reste sans état) : c'est le ticket de scan à usage
    unique (voir tickets.py) qui empêche d'enregistrer deux présences
    avec le même scan.

    Pas de format hérité : un QR rotatif ne vit que 20 secondes, il n'y
    a aucun support ancien à préserver.
    """
    fenetre_actuelle = fenetre_temps_actuelle()
    if fenetre_recue not in (fenetre_actuelle, fenetre_actuelle - 1):
        return False

    cle = _cle_pour_version(version_recue)
    if cle is None:
        return False

    attendue = _hmac_hex(cle, _message_rotatif(uuid_carte, fenetre_recue))
    return hmac.compare_digest(attendue, signature_recue)


# =====================================================================
# MOTS DE PASSE TEMPORAIRES
# =====================================================================

# Sans caractères ambigus (0/O, 1/l/I) : le mot de passe est souvent
# recopié à la main depuis un email ou un écran.
_ALPHABET_MDP_TEMPORAIRE = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generer_mot_de_passe_temporaire(longueur: int = 12) -> str:
    """
    Mot de passe temporaire tiré par le CSPRNG du système (secrets),
    jamais dérivé de données connues (nom, prénom...). 12 caractères
    sur 55 symboles ≈ 69 bits d'entropie, hors de portée d'une attaque
    en ligne même sans limitation de débit.
    """
    return "AP2A-" + "".join(secrets.choice(_ALPHABET_MDP_TEMPORAIRE) for _ in range(longueur))


# =====================================================================
# TÉLÉVERSEMENT D'IMAGES
# =====================================================================

TAILLE_MAX_IMAGE_OCTETS = 5 * 1024 * 1024
FORMATS_IMAGE_AUTORISES = {"JPEG", "PNG", "WEBP"}


def erreur_image_televersee(fichier) -> str | None:
    """
    Valide une photo téléversée AVANT de l'enregistrer : taille bornée
    et contenu réellement décodable comme une image d'un format
    autorisé (on ne se fie ni à l'extension ni au Content-Type envoyés
    par le client). Retourne un message d'erreur, ou None si l'image
    est acceptable.
    """
    from PIL import Image, UnidentifiedImageError

    if fichier.size > TAILLE_MAX_IMAGE_OCTETS:
        return "Image trop volumineuse (5 Mo maximum)"
    try:
        image = Image.open(fichier)
        format_image = image.format
        image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return "Fichier image invalide"
    finally:
        fichier.seek(0)
    if format_image not in FORMATS_IMAGE_AUTORISES:
        return "Format d'image non autorisé (JPEG, PNG ou WebP)"
    return None


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


def envoyer_email_arriere_plan(sujet: str, message: str, destinataires: list[str]) -> None:
    """
    Envoie un email dans un thread séparé, SANS bloquer la requête HTTP
    en cours. Un envoi SMTP synchrone prend plusieurs secondes (Gmail
    depuis un serveur cloud peut être encore plus lent, voire hors
    délai) - assez pour dépasser le timeout du proxy d'hébergement
    (Render) et faire échouer toute l'action (créer un membre, renvoyer
    des identifiants...) alors qu'elle a en réalité réussi côté serveur.

    Contrepartie assumée : on ne peut plus garantir de façon synchrone
    que l'email a été livré au moment où la réponse HTTP part - c'est
    le compromis standard de ce type d'action (comme "email de
    réinitialisation envoyé" sur la plupart des sites).
    """
    def _envoyer():
        import logging
        from django.core.mail import send_mail
        logger = logging.getLogger(__name__)
        try:
            send_mail(
                subject=sujet,
                message=message,
                from_email=None,
                recipient_list=destinataires,
                fail_silently=False,
            )
            logger.info("Email envoyé avec succès à %s : %s", destinataires, sujet)
        except Exception:
            logger.exception("Échec de l'envoi d'email à %s : %s", destinataires, sujet)

    threading.Thread(target=_envoyer, daemon=True).start()


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
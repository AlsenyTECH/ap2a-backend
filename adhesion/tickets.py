"""
adhesion/tickets.py

Deux jetons signés de courte durée, émis par le serveur pour lui-même :

1. TICKET DE SCAN - lie la confirmation d'une présence à une
   vérification de carte réellement réussie.

   Sans lui, POST /confirmer-entree/ acceptait un simple id_membre :
   la signature HMAC de la carte ne protégeait que l'affichage, et
   n'importe quel contrôleur (ou client modifié) pouvait marquer
   présent un membre jamais scanné. Désormais :

       vérification OK  ->  ticket = Sign(type, id, contrôleur, méthode, nonce, t)
       confirmation     ->  exige ce ticket, encore valide, jamais utilisé,
                            émis pour CE contrôleur et CETTE personne.

   - Authenticité / intégrité : signature HMAC-SHA256 (django.core.signing)
     avec un sel propre à cet usage, donc une clé dérivée distincte de
     toutes les autres signatures de l'application.
   - Fraîcheur : horodatage signé, rejet après TICKET_SCAN_DUREE_SECONDES.
   - Unicité (anti-rejeu) : nonce aléatoire de 128 bits, enregistré à la
     consommation dans TicketScanConsomme (contrainte UNIQUE en base).
   - Liaison au contexte : le ticket nomme le compte contrôleur qui l'a
     obtenu et le mode de vérification (SCAN ou MANUEL).

2. LIEN DE TÉLÉCHARGEMENT - remplace l'ancien ?jeton=<jeton de session>
   dans les URL de téléchargement direct. Le jeton de session finissait
   dans les journaux serveur/proxy, l'historique du navigateur et
   l'en-tête Referer. Le lien signé n'authentifie qu'UNE route de
   téléchargement, pour UN compte, pendant LIEN_TELECHARGEMENT_DUREE_SECONDES.
"""

import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone

SEL_TICKET_SCAN = "ap2a.ticket-scan.v1"
SEL_LIEN_TELECHARGEMENT = "ap2a.lien-telechargement.v1"

TYPE_MEMBRE = "membre"
TYPE_PARTICIPANT = "participant"

MODE_SCAN = "SCAN"
MODE_MANUEL = "MANUEL"


class TicketInvalide(Exception):
    """Ticket absent, falsifié, expiré, déjà utilisé ou hors contexte."""


class TicketDejaUtilise(TicketInvalide):
    pass


def emettre_ticket_scan(type_personne: str, id_personne: int, compte_controleur, mode: str) -> str:
    return signing.dumps(
        {
            "t": type_personne,
            "id": id_personne,
            "c": compte_controleur.id_compte,
            "m": mode,
            "n": secrets.token_urlsafe(16),
        },
        salt=SEL_TICKET_SCAN,
    )


def lire_ticket_scan(ticket: str, type_personne: str, compte_controleur, mode: str) -> dict:
    """
    Vérifie signature, fraîcheur et contexte du ticket, SANS le
    consommer. Retourne le contenu (dont l'id de la personne).
    """
    if not ticket or not isinstance(ticket, str):
        raise TicketInvalide("Ticket de scan manquant - scannez ou vérifiez d'abord la carte.")
    try:
        contenu = signing.loads(ticket, salt=SEL_TICKET_SCAN, max_age=settings.TICKET_SCAN_DUREE_SECONDES)
    except signing.SignatureExpired:
        raise TicketInvalide("Ticket de scan expiré - scannez à nouveau la carte.")
    except signing.BadSignature:
        raise TicketInvalide("Ticket de scan invalide.")

    if contenu.get("t") != type_personne or contenu.get("m") != mode:
        raise TicketInvalide("Ticket de scan non valable pour cette opération.")
    if contenu.get("c") != compte_controleur.id_compte:
        raise TicketInvalide("Ce ticket de scan a été émis pour un autre contrôleur.")
    return contenu


def consommer_ticket_scan(contenu: dict, enregistrer_presence):
    """
    Consomme le nonce du ticket ET enregistre la présence de façon
    atomique : si l'enregistrement échoue (personne déjà présente...),
    le ticket n'est pas brûlé ; si le nonce a déjà servi, rien n'est
    enregistré.

    `enregistrer_presence` est une fonction sans argument qui crée la
    présence ; ses exceptions (IntegrityError comprise) remontent telles
    quelles à l'appelant.
    """
    from .models import TicketScanConsomme

    TicketScanConsomme.objects.filter(
        date_consommation__lt=timezone.now() - timedelta(seconds=2 * settings.TICKET_SCAN_DUREE_SECONDES)
    ).delete()

    class _EchecPresence(Exception):
        pass

    try:
        with transaction.atomic():
            TicketScanConsomme.objects.create(nonce=contenu["n"])
            try:
                with transaction.atomic():
                    resultat = enregistrer_presence()
            except Exception as e:
                raise _EchecPresence() from e
    except _EchecPresence as e:
        raise e.__cause__
    except IntegrityError:
        raise TicketDejaUtilise("Ce ticket de scan a déjà été utilisé - scannez à nouveau la carte.")
    return resultat


def emettre_lien_telechargement(compte, chemin: str) -> str:
    return signing.dumps({"c": compte.id_compte, "p": chemin}, salt=SEL_LIEN_TELECHARGEMENT)


def lire_lien_telechargement(valeur: str, chemin: str) -> int | None:
    """Retourne l'id du compte si le lien est valide pour ce chemin, sinon None."""
    try:
        contenu = signing.loads(
            valeur, salt=SEL_LIEN_TELECHARGEMENT, max_age=settings.LIEN_TELECHARGEMENT_DUREE_SECONDES
        )
    except signing.BadSignature:
        return None
    if contenu.get("p") != chemin:
        return None
    return contenu.get("c")

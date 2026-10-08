"""Fabriques de données partagées par les tests."""

import itertools
import uuid as uuid_lib

from django.utils import timezone
from rest_framework.test import APIClient

from adhesion.models import (
    Carte, Cohorte, Compte, Controleur, Evenement, Formation, InscriptionCohorte,
    Jeton, Membre, Participant, PermissionAdmin, Seance, SeanceCohorte,
)

MOT_DE_PASSE = "Un-mot-de-passe-solide-42"

_compteur = itertools.count(1)


def creer_compte(est_admin=False, est_super_admin=False, permissions=(), email=None):
    n = next(_compteur)
    compte = Compte(
        email=email or f"compte{n}@exemple.org",
        nom=f"Nom{n}",
        prenom=f"Prenom{n}",
        est_admin=est_admin or est_super_admin,
        est_super_admin=est_super_admin,
    )
    compte.definir_mot_de_passe(MOT_DE_PASSE)
    compte.save()
    for code in permissions:
        PermissionAdmin.objects.create(compte=compte, code_permission=code)
    return compte


def creer_membre(compte=None):
    compte = compte or creer_compte()
    return Membre.objects.create(
        compte=compte,
        numero_adherent=f"ADH-{compte.id_compte:04d}",
        date_adhesion=timezone.now().date(),
    )


def creer_carte(membre, type_carte="QR"):
    return Carte.objects.create(uuid=uuid_lib.uuid4(), type_carte=type_carte, id_version_cle=1, membre=membre)


def creer_controleur(compte=None, evenement=None):
    compte = compte or creer_compte()
    return Controleur.objects.create(
        compte=compte, date_nomination=timezone.now().date(), evenement_assigne=evenement
    )


def creer_evenement(organisateur):
    evenement = Evenement.objects.create(
        titre="Assemblée générale", lieu="Dakar", type_evenement="MEETING",
        compte_organisateur=organisateur,
    )
    seances = [
        Seance.objects.create(evenement=evenement, date_seance=timezone.now(), numero_ordre=i)
        for i in (1, 2)
    ]
    return evenement, seances


def creer_cohorte(nombre_seances=2):
    formation = Formation.objects.create(titre="Cryptographie appliquée")
    aujourdhui = timezone.now().date()
    cohorte = Cohorte.objects.create(
        formation=formation, code_cohorte=f"COH-{next(_compteur)}",
        date_debut=aujourdhui, date_fin=aujourdhui, lieu="Dakar",
    )
    seances = [
        SeanceCohorte.objects.create(cohorte=cohorte, date_seance=timezone.now(), numero_ordre=i)
        for i in range(1, nombre_seances + 1)
    ]
    return cohorte, seances


def creer_participant(cohorte=None, membre=None):
    participant = Participant.objects.create(
        nom="Diop", prenom="Awa", uuid=uuid_lib.uuid4(), id_version_cle=1, membre=membre,
    )
    if cohorte is not None:
        InscriptionCohorte.objects.create(participant=participant, cohorte=cohorte, source="MANUEL")
    return participant


def client_pour(compte):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {Jeton.ouvrir_session(compte)}")
    return client

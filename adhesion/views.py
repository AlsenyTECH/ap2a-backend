"""
adhesion/views.py

Vue de connexion : point d'entrée qui transforme un couple
email/mot_de_passe en un jeton de session, utilisé ensuite pour
toutes les requêtes authentifiées (voir authentication.py).
"""

import uuid as uuid_lib
import unicodedata
import re
import secrets
import openpyxl

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import IntegrityError
from django.db.models import Count, Q, Min, Max
from django.db.models.deletion import ProtectedError
from django.conf import settings
from django.http import HttpResponse

from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework import status

from .models import (
    Compte, Jeton, Carte, Membre, Evenement, Seance, Participe,
    Section, Controleur, JournalAudit,
    Formation, Cohorte, Participant, InscriptionCohorte, SeanceCohorte, PresenceCohorte,
    PermissionAdmin, Notification, ConfirmationEvenement,
)
from .permissions import EstMembre, EstAdmin, EstAdminPrincipal, EstAuthentifie, PeutControler, EstControleur
from .utils import (
    generer_signature, verifier_signature, construire_contenu_carte, reconstruire_contenu_carte,
    construire_contenu_rotatif, verifier_signature_temporelle,
    synchroniser_statuts_cohortes, erreur_si_cohorte_verrouillee, TRANSITIONS_COHORTE_AUTORISEES,
)
from .rapports import excel_depuis_tableau, pdf_depuis_tableau


@api_view(["POST"])
@permission_classes([AllowAny])  # accessible sans être déjà connecté
def vue_connexion(request):
    """
    POST /api/login/
    Corps attendu (JSON) : {"email": "...", "mot_de_passe": "..."}
    """
    email = request.data.get("email", "").strip()
    mot_de_passe = request.data.get("mot_de_passe")

    if not email or not mot_de_passe:
        return Response(
            {"erreur": "Identifiant et mot_de_passe sont requis"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Lookup : essayer d'abord par email, puis par numéro adhérent
    try:
        compte = Compte.objects.get(email=email)
    except Compte.DoesNotExist:
        # Tenter de trouver par numéro adhérent
        try:
            membre = Membre.objects.select_related("compte").get(numero_adherent=email)
            compte = membre.compte
        except Membre.DoesNotExist:
            return Response(
                {"erreur": "Identifiant ou mot de passe incorrect"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

    if not compte.verifier_mot_de_passe(mot_de_passe):
        return Response(
            {"erreur": "Identifiant ou mot de passe incorrect"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if compte.statut_compte != "ACTIF":
        return Response(
            {"erreur": "Ce compte est suspendu"},
            status=status.HTTP_403_FORBIDDEN,
        )

    # update_or_create : si un jeton existe déjà pour ce compte, on le
    # remplace (cohérent avec le choix OneToOneField sur Jeton : une
    # seule session active à la fois, toute reconnexion invalide
    # l'ancien jeton).
    jeton, _ = Jeton.objects.update_or_create(
        compte=compte,
        defaults={"cle": Jeton.generer_cle()},
    )

    return Response({
        "jeton": jeton.cle,
        "nom": compte.nom,
        "prenom": compte.prenom,
        "est_admin": compte.est_admin,
        "est_super_admin": compte.est_super_admin,
        "est_membre": hasattr(compte, "membre"),
        "est_controleur": hasattr(compte, "controleur"),
        "doit_changer_mot_de_passe": compte.doit_changer_mot_de_passe,
        # Permissions granulaires pour les admins désignés
        "permissions": (
            [code for code, _ in PermissionAdmin.PERMISSION_CHOICES]
            if compte.est_super_admin
            else list(
                PermissionAdmin.objects.filter(compte=compte)
                .values_list("code_permission", flat=True)
            ) if compte.est_admin
            else []
        ),
    })


@api_view(["GET"])
@permission_classes([PeutControler])
def vue_verifier(request, uuid_carte, version, signature):
    """
    GET /api/verifier/<uuid_carte>/<version>/<signature>/
    Vérification du QR STATIQUE (carte physique imprimée, ou carte
    virtuelle avant l'ajout du QR rotatif). Signature valable
    indéfiniment tant que la clé n'est pas retirée du trousseau.
    """
    uuid_str = str(uuid_carte)

    if not verifier_signature(uuid_str, signature, version):
        return Response(
            {"valide": False, "erreur": "Signature invalide"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    return _resultat_verification_carte(uuid_carte)


@api_view(["GET"])
@permission_classes([PeutControler])
def vue_verifier_rotatif(request, uuid_carte, version, fenetre, signature):
    """
    GET /api/verifier-rotatif/<uuid_carte>/<version>/<fenetre>/<signature>/
    Vérification du QR ROTATIF (carte VIRTUELLE uniquement, affichée
    en direct dans l'appli - jamais imprimée). La signature n'est
    valable que ~20 secondes (fenêtre courante + une de tolérance),
    ce qui rend une photo du QR quasi inutilisable : elle expire
    avant qu'un attaquant ait pu s'en servir.
    """
    uuid_str = str(uuid_carte)

    if not verifier_signature_temporelle(uuid_str, fenetre, version, signature):
        return Response(
            {"valide": False, "erreur": "QR expiré ou signature invalide - redemandez à la personne de rafraîchir sa carte"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    return _resultat_verification_carte(uuid_carte)


def _resultat_verification_carte(uuid_carte):
    """
    Logique commune aux deux endpoints de vérification (statique et
    rotatif) : une fois la signature authentifiée (peu importe
    laquelle des deux méthodes), la suite est identique - chercher la
    carte, vérifier les statuts, renvoyer l'identité.
    """
    try:
        carte = Carte.objects.select_related("membre__compte").get(uuid=uuid_carte)
    except Carte.DoesNotExist:
        return Response(
            {"valide": False, "erreur": "Carte introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if carte.statut_carte != "ACTIVE":
        return Response(
            {"valide": False, "erreur": f"Carte {carte.get_statut_carte_display().lower()}"},
            status=status.HTTP_403_FORBIDDEN,
        )

    membre = carte.membre
    if membre.statut_adhesion != "ACTIF":
        return Response(
            {"valide": False, "erreur": f"Adhésion : {membre.get_statut_adhesion_display().lower()}"},
            status=status.HTTP_403_FORBIDDEN,
        )

    return Response({
        "valide": True,
        "id_membre": membre.id_membre,
        "nom": membre.compte.nom,
        "prenom": membre.compte.prenom,
        "numero_adherent": membre.numero_adherent,
        "photo": membre.photo.url if membre.photo else None,
    })


@api_view(["POST"])
@permission_classes([PeutControler])
def vue_confirmer_entree(request):
    """
    POST /api/confirmer-entree/
    Corps attendu : {"id_membre": ..., "id_seance": ..., "methode_scan": "QR"|"NFC"|"MANUEL"}

    CHANGÉ : id_seance remplace id_evenement - une présence est
    désormais rattachée à un jour précis (une séance), pas à
    l'événement dans son ensemble, pour permettre le suivi jour par
    jour d'une formation multi-jours.
    """
    id_membre = request.data.get("id_membre")
    id_seance = request.data.get("id_seance")
    methode_scan = request.data.get("methode_scan")

    if not all([id_membre, id_seance, methode_scan]):
        return Response(
            {"erreur": "id_membre, id_seance et methode_scan sont requis"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if methode_scan not in dict(Participe.METHODE_CHOICES):
        return Response(
            {"erreur": "methode_scan doit être QR, NFC ou MANUEL"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        membre = Membre.objects.select_related("compte").get(id_membre=id_membre)
    except Membre.DoesNotExist:
        return Response(
            {"erreur": "Membre introuvable"}, status=status.HTTP_404_NOT_FOUND
        )

    try:
        seance = Seance.objects.select_related("evenement").get(id_seance=id_seance)
    except Seance.DoesNotExist:
        return Response(
            {"erreur": "Séance introuvable"}, status=status.HTTP_404_NOT_FOUND
        )

    if seance.evenement.est_termine:
        return Response(
            {"erreur": "Cet événement est terminé, plus aucun scan n'est accepté"},
            status=status.HTTP_403_FORBIDDEN,
        )

    controleur, _ = Controleur.objects.get_or_create(
        compte=request.user,
        defaults={"date_nomination": timezone.now().date()},
    )

    # Un contrôleur (non-admin) ne peut scanner QUE pour l'événement
    # auquel il est actuellement assigné - décision prise ensemble
    # pour que la désactivation en fin d'événement ait un effet réel,
    # sans pour autant suspendre tout le compte (qui peut aussi être
    # celui d'un membre ou d'un admin par ailleurs). Les admins ne
    # sont jamais soumis à cette restriction.
    if not request.user.est_admin:
        if controleur.evenement_assigne_id != seance.evenement_id:
            return Response(
                {"erreur": "Vous n'êtes pas assigné à cet événement. Contactez un administrateur."},
                status=status.HTTP_403_FORBIDDEN,
            )

    try:
        participation = Participe.objects.create(
            membre=membre,
            seance=seance,
            heure_arrivee=timezone.now(),
            methode_scan=methode_scan,
            controleur_scan=controleur,
        )
    except IntegrityError:
        # Violation de unique_together (membre, seance) : ce membre
        # est déjà enregistré présent à CETTE séance précise - il
        # peut en revanche être enregistré à une autre séance du
        # même événement (jour suivant, par exemple).
        return Response(
            {"erreur": "Ce membre est déjà enregistré comme présent à cette séance"},
            status=status.HTTP_409_CONFLICT,
        )

    return Response(
        {
            "confirme": True,
            "membre": f"{membre.compte.prenom} {membre.compte.nom}",
            "evenement": seance.evenement.titre,
            "seance": seance.numero_ordre,
            "heure_arrivee": participation.heure_arrivee,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([EstMembre])
def vue_declarer_perte(request):
    """
    POST /api/carte/declarer-perte/
    Corps : {} (aucun champ requis - concerne toujours la carte
    physique NFC, jamais la carte QR virtuelle).

    Ne concerne QUE la carte physique NFC remise par l'association :
    elle est bloquée, mais AUCUNE nouvelle carte n'est générée
    automatiquement (c'est un humain, à l'association, qui en émet
    une nouvelle en personne). La carte QR virtuelle du membre n'est
    jamais affectée et continue de fonctionner sans interruption.
    """
    membre = request.user.membre

    carte_nfc = membre.cartes.filter(type_carte="NFC", statut_carte="ACTIVE").first()
    if carte_nfc is None:
        return Response(
            {"erreur": "Aucune carte physique (NFC) active trouvée"}, status=status.HTTP_404_NOT_FOUND
        )

    carte_nfc.statut_carte = "PERDUE"
    carte_nfc.save(update_fields=["statut_carte"])

    JournalAudit.objects.create(
        type_action="perte_carte",
        description=f"Carte physique {carte_nfc.uuid} déclarée perdue (réémission manuelle par l'association)",
        compte_auteur=request.user,
    )

    return Response({"carte_physique_bloquee": str(carte_nfc.uuid)})


@api_view(["GET"])
@permission_classes([PeutControler])
def vue_verifier_manuel(request, numero_adherent):
    """
    GET /api/verifier-manuel/<numero_adherent>/
    Solution de secours si le scan échoue. Contrairement au scan
    classique (jamais journalisé), CHAQUE recherche manuelle est
    tracée - succès ou échec - car c'est un point d'entrée plus
    sensible (décision prise ensemble).
    """
    try:
        membre = Membre.objects.select_related("compte").get(
            numero_adherent=numero_adherent
        )
    except Membre.DoesNotExist:
        JournalAudit.objects.create(
            type_action="recherche_manuelle",
            description=f"Numéro recherché : {numero_adherent} - introuvable",
            compte_auteur=request.user,
        )
        return Response(
            {"trouve": False, "erreur": "Membre introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    JournalAudit.objects.create(
        type_action="recherche_manuelle",
        description=(
            f"Numéro recherché : {numero_adherent} - trouvé "
            f"({membre.compte.prenom} {membre.compte.nom})"
        ),
        compte_auteur=request.user,
    )

    return Response({
        "trouve": True,
        "id_membre": membre.id_membre,
        "nom": membre.compte.nom,
        "prenom": membre.compte.prenom,
        "statut_adhesion": membre.statut_adhesion,
        "photo": membre.photo.url if membre.photo else None,
    })


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_bloquer_carte(request, id_carte):
    """
    POST /api/admin/carte/<id_carte>/bloquer/
    Bloque la carte ET suspend le compte associé - un membre bloqué
    par un admin ne doit plus pouvoir se connecter à l'appli non plus,
    pas seulement voir sa carte refusée au scan (décision prise
    ensemble : blocage disciplinaire complet, jusqu'à réintégration).
    """
    try:
        carte = Carte.objects.select_related("membre__compte").get(id_carte=id_carte)
    except Carte.DoesNotExist:
        return Response({"erreur": "Carte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    carte.statut_carte = "BLOQUEE"
    carte.save()

    compte_membre = carte.membre.compte
    compte_membre.statut_compte = "SUSPENDU"
    compte_membre.save()

    JournalAudit.objects.create(
        type_action="blocage_carte",
        description=f"Carte {carte.uuid} bloquée, compte {compte_membre.email} suspendu",
        compte_auteur=request.user,
    )
    return Response({"id_carte": carte.id_carte, "statut_carte": carte.statut_carte})


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_activer_carte(request, id_carte):
    """
    POST /api/admin/carte/<id_carte>/activer/
    Réactive la carte ET réintègre le compte (statut_compte -> ACTIF) -
    symétrique du blocage ci-dessus.
    """
    try:
        carte = Carte.objects.select_related("membre__compte").get(id_carte=id_carte)
    except Carte.DoesNotExist:
        return Response({"erreur": "Carte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    carte.statut_carte = "ACTIVE"
    carte.save()

    compte_membre = carte.membre.compte
    compte_membre.statut_compte = "ACTIF"
    compte_membre.save()

    JournalAudit.objects.create(
        type_action="reactivation_carte",
        description=f"Carte {carte.uuid} réactivée, compte {compte_membre.email} réintégré",
        compte_auteur=request.user,
    )
    return Response({"id_carte": carte.id_carte, "statut_carte": carte.statut_carte})


@api_view(["POST"])
@permission_classes([EstAdminPrincipal])
def vue_nommer_admin(request):
    """
    POST /api/admin/nommer-admin/
    Corps : {"email_compte": "..."}
    Réservé à l'admin principal (règle métier décidée ensemble).
    """
    email = request.data.get("email_compte")
    try:
        compte_cible = Compte.objects.get(email=email)
    except Compte.DoesNotExist:
        return Response({"erreur": "Compte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    # Règle décidée ensemble : tout admin est forcément membre. On
    # l'impose ici, pas seulement dans la documentation - sinon rien
    # ne garantit qu'elle soit respectée pour les futurs admins.
    if not hasattr(compte_cible, "membre"):
        return Response(
            {"erreur": "Seul un compte ayant une fiche Membre peut être nommé admin"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    compte_cible.est_admin = True
    compte_cible.compte_nommant = request.user
    compte_cible.save()

    JournalAudit.objects.create(
        type_action="nomination_admin",
        description=f"{compte_cible.email} nommé admin",
        compte_auteur=request.user,
    )
    return Response({"email": compte_cible.email, "est_admin": True})


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_creer_controleur(request):
    """
    POST /api/admin/creer-controleur/
    Corps : soit {"email_membre": "..."} pour désigner un membre
    existant comme contrôleur, soit {"email", "mot_de_passe", "nom",
    "prenom"} pour un contrôleur externe (pas membre du parti).
    """
    email_membre = request.data.get("email_membre")

    if email_membre:
        try:
            compte = Compte.objects.get(email=email_membre)
        except Compte.DoesNotExist:
            return Response(
                {"erreur": "Compte introuvable"}, status=status.HTTP_404_NOT_FOUND
            )
    else:
        champs_requis = ["email", "mot_de_passe", "nom", "prenom"]
        if not all(request.data.get(c) for c in champs_requis):
            return Response(
                {"erreur": f"Champs requis : {', '.join(champs_requis)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        compte = Compte(
            email=request.data["email"],
            nom=request.data["nom"],
            prenom=request.data["prenom"],
        )
        compte.definir_mot_de_passe(request.data["mot_de_passe"])
        # Mot de passe choisi par l'admin, pas par la personne elle-même
        # -> changement obligatoire à la première connexion.
        compte.doit_changer_mot_de_passe = True
        compte.save()

    if hasattr(compte, "controleur"):
        return Response(
            {"erreur": "Ce compte est déjà contrôleur"}, status=status.HTTP_400_BAD_REQUEST
        )

    evenement_assigne = None
    id_evenement = request.data.get("id_evenement")
    if id_evenement:
        try:
            evenement_assigne = Evenement.objects.get(pk=id_evenement, est_termine=False)
        except Evenement.DoesNotExist:
            return Response(
                {"erreur": "Événement introuvable ou déjà terminé"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    controleur = Controleur.objects.create(
        date_nomination=timezone.now().date(),
        zone_affectation=request.data.get("zone_affectation"),
        compte=compte,
        evenement_assigne=evenement_assigne,
    )

    JournalAudit.objects.create(
        type_action="creation_controleur",
        description=f"{compte.email} désigné contrôleur",
        compte_auteur=request.user,
    )
    return Response(
        {"id_controleur": controleur.id_controleur, "email": compte.email},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_creer_evenement(request):
    """
    POST /api/admin/evenement/
    Corps : {"titre", "lieu", "type_evenement", "dates": [iso, iso, ...]}

    CHANGÉ : "date_evenement" (une seule date) devient "dates" (une
    liste) - chaque date de la liste devient une SEANCE numérotée
    dans l'ordre. Un événement ponctuel envoie une liste à un seul
    élément ; une formation sur 5 jours en envoie 5.
    """
    donnees = request.data
    champs_requis = ["titre", "lieu", "type_evenement", "dates"]
    if not all(donnees.get(c) for c in champs_requis):
        return Response(
            {"erreur": f"Champs requis : {', '.join(champs_requis)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    dates = donnees["dates"]
    if not isinstance(dates, list) or len(dates) == 0:
        return Response(
            {"erreur": "'dates' doit être une liste non vide d'au moins une date"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    mode_inscription = donnees.get("mode_inscription", "OUVERT")
    ids_membres_cibles = donnees.get("membres_cibles") or []
    if mode_inscription == "RESTREINT" and not ids_membres_cibles:
        return Response(
            {"erreur": "Sélectionnez au moins un membre pour un événement restreint"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    evenement = Evenement.objects.create(
        titre=donnees["titre"],
        lieu=donnees["lieu"],
        type_evenement=donnees["type_evenement"],
        mode_inscription=mode_inscription,
        capacite_max=donnees.get("capacite_max"),
        compte_organisateur=request.user,
    )

    for numero, date_str in enumerate(dates, start=1):
        Seance.objects.create(evenement=evenement, date_seance=date_str, numero_ordre=numero)

    if mode_inscription == "RESTREINT":
        membres_cibles = Membre.objects.filter(id_membre__in=ids_membres_cibles)
        evenement.membres_cibles.set(membres_cibles)
        Notification.objects.bulk_create([
            Notification(
                compte_destinataire=m.compte,
                titre="Nouvel événement",
                corps=f"Vous êtes invité(e) à « {evenement.titre} ».",
                type_notification="INFO",
                lien_action="/membre/evenements",
            )
            for m in membres_cibles
        ])

    return Response(
        {
            "id_evenement": evenement.id_evenement,
            "titre": evenement.titre,
            "nombre_seances": len(dates),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["PATCH", "DELETE"])
@permission_classes([EstAdmin])
def vue_modifier_ou_supprimer_evenement(request, id_evenement):
    """
    PATCH /api/admin/evenement/<id>/modifier/
        Modification complète d'un événement : titre, lieu, type, description,
        mode_inscription, capacite_max. Si "dates" est fourni, les séances
        existantes sont recréées (uniquement si aucune présence enregistrée
        sur les anciennes séances).
    DELETE /api/admin/evenement/<id>/modifier/
        Suppression uniquement si aucune présence n'a été enregistrée.
    """
    try:
        evenement = Evenement.objects.get(id_evenement=id_evenement)
    except Evenement.DoesNotExist:
        return Response({"erreur": "Événement introuvable"}, status=status.HTTP_404_NOT_FOUND)

    nb_presences = Participe.objects.filter(seance__evenement=evenement).count()

    if request.method == "DELETE":
        if nb_presences > 0:
            return Response(
                {"erreur": f"Impossible de supprimer : {nb_presences} présence(s) enregistrée(s). Annulez l'événement à la place."},
                status=status.HTTP_409_CONFLICT,
            )
        titre = evenement.titre
        evenement.delete()
        JournalAudit.objects.create(
            type_action="suppression_evenement",
            description=f"Événement supprimé : {titre}",
            compte_auteur=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # PATCH
    donnees = request.data
    champs_modifiables = [
        "titre", "lieu", "type_evenement", "description",
        "mode_inscription", "capacite_max",
    ]
    for champ in champs_modifiables:
        if champ in donnees:
            setattr(evenement, champ, donnees[champ])

    # Modification des dates de séances (reporter)
    if "dates" in donnees:
        dates = donnees["dates"]
        if isinstance(dates, list) and len(dates) > 0:
            if nb_presences > 0:
                return Response(
                    {"erreur": "Impossible de modifier les dates : des présences sont déjà enregistrées."},
                    status=status.HTTP_409_CONFLICT,
                )
            evenement.seances.all().delete()
            for numero, date_str in enumerate(dates, start=1):
                Seance.objects.create(evenement=evenement, date_seance=date_str, numero_ordre=numero)

    # Modification des membres cibles
    if "membres_cibles" in donnees:
        ids = donnees["membres_cibles"]
        if isinstance(ids, list):
            membres_cibles = Membre.objects.filter(id_membre__in=ids)
            evenement.membres_cibles.set(membres_cibles)

    evenement.save()

    JournalAudit.objects.create(
        type_action="modification_evenement",
        description=f"Événement modifié : {evenement.titre}",
        compte_auteur=request.user,
    )

    return Response({
        "id_evenement": evenement.id_evenement,
        "titre": evenement.titre,
        "nombre_seances": evenement.seances.count(),
    })


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_annuler_evenement(request, id_evenement):
    """
    POST /api/admin/evenement/<id>/annuler/
    Annule un événement (le marque comme est_annule=True). Les contrôleurs
    assignés sont désassignés. Les membres sont notifiés.
    """
    try:
        evenement = Evenement.objects.get(id_evenement=id_evenement)
    except Evenement.DoesNotExist:
        return Response({"erreur": "Événement introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if evenement.est_annule:
        return Response({"erreur": "Cet événement est déjà annulé"}, status=status.HTTP_400_BAD_REQUEST)

    evenement.est_annule = True
    evenement.est_termine = True
    evenement.date_fin = timezone.now()
    evenement.save()

    Controleur.objects.filter(evenement_assigne=evenement).update(evenement_assigne=None)

    # Notification à tous les membres confirmés
    confirmations = ConfirmationEvenement.objects.filter(
        evenement=evenement
    ).exclude(statut="ANNULE").select_related("membre__compte")
    Notification.objects.bulk_create([
        Notification(
            compte_destinataire=c.membre.compte,
            titre="Événement annulé",
            corps=f"L'événement « {evenement.titre} » a été annulé.",
            type_notification="ALERTE",
            lien_action="/membre/evenements",
        )
        for c in confirmations
    ])

    JournalAudit.objects.create(
        type_action="annulation_evenement",
        description=f"Événement annulé : {evenement.titre}",
        compte_auteur=request.user,
    )

    return Response({"id_evenement": evenement.id_evenement, "est_annule": True})


@api_view(["GET"])
@permission_classes([PeutControler])
def vue_liste_seances(request, id_evenement):
    """
    GET /api/evenement/<id_evenement>/seances/
    Liste les séances d'un événement, pour que le contrôleur choisisse
    quel jour précis il est en train de contrôler (transparent pour
    un événement à une seule séance - la sélection se fait alors
    automatiquement côté Flutter, sans écran supplémentaire).
    """
    try:
        evenement = Evenement.objects.get(id_evenement=id_evenement)
    except Evenement.DoesNotExist:
        return Response({"erreur": "Événement introuvable"}, status=status.HTTP_404_NOT_FOUND)

    seances = evenement.seances.order_by("numero_ordre")
    return Response([
        {
            "id_seance": s.id_seance,
            "numero_ordre": s.numero_ordre,
            "date_seance": s.date_seance,
        }
        for s in seances
    ])


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_statistiques(request):
    """GET /api/admin/statistiques/"""
    par_section = (
        Membre.objects.values("section__nom_section")
        .annotate(nombre=Count("id_membre"))
        .order_by("-nombre")
    )
    par_fonction = (
        Membre.objects.values("fonction_association")
        .annotate(nombre=Count("id_membre"))
        .order_by("-nombre")
    )
    libelles_fonction = dict(Membre.FONCTION_CHOICES)
    repartition_par_fonction = [
        {
            "fonction_association": ligne["fonction_association"],
            "fonction_association_libelle": libelles_fonction.get(ligne["fonction_association"], ligne["fonction_association"]),
            "nombre": ligne["nombre"],
        }
        for ligne in par_fonction
    ]

    return Response({
        "total_membres": Membre.objects.count(),
        "membres_actifs": Membre.objects.filter(statut_adhesion="ACTIF").count(),
        "repartition_par_section": list(par_section),
        "repartition_par_fonction": repartition_par_fonction,
    })


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_journal_audit(request):
    """
    GET /api/admin/journal/?type_action=...&date_debut=YYYY-MM-DD
        &date_fin=YYYY-MM-DD&page=1

    Paginé (50 par page) et filtrable : indispensable dès que le
    volume de lignes devient important (des mois d'activité peuvent
    représenter des centaines de milliers d'entrées).
    """
    entrees = JournalAudit.objects.select_related("compte_auteur").order_by(
        "-date_action"
    )

    type_action = request.query_params.get("type_action")
    if type_action:
        entrees = entrees.filter(type_action=type_action)

    date_debut = request.query_params.get("date_debut")
    if date_debut:
        entrees = entrees.filter(date_action__date__gte=date_debut)

    date_fin = request.query_params.get("date_fin")
    if date_fin:
        entrees = entrees.filter(date_action__date__lte=date_fin)

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1

    taille_page = 50
    debut = (page - 1) * taille_page
    fin = debut + taille_page

    # .count() puis slicing : 2 requêtes SQL, mais chacune reste rapide
    # (COUNT et LIMIT/OFFSET sont optimisés par le SGBD), bien plus
    # sûr que de charger toutes les lignes en mémoire Python pour les
    # compter ou les découper soi-même.
    total = entrees.count()
    entrees_page = entrees[debut:fin]

    return Response({
        "total": total,
        "page": page,
        "taille_page": taille_page,
        "nombre_pages": (total + taille_page - 1) // taille_page,
        "resultats": [
            {
                "type_action": e.type_action,
                "date_action": e.date_action,
                "description": e.description,
                "auteur": e.compte_auteur.email,
            }
            for e in entrees_page
        ],
    })


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_recherche_comptes(request):
    """
    GET /api/admin/recherche-comptes/?q=...
    Autocomplétion par nom, prénom ou email (au moins 2 caractères) -
    utilisée pour la nomination d'admin et la création de contrôleur
    (cas "membre existant"), pour éviter d'avoir à taper un email
    exact de mémoire.
    """
    terme = request.query_params.get("q", "").strip()
    if len(terme) < 2:
        return Response([])  # évite de renvoyer TOUS les comptes sur une requête vide

    comptes = Compte.objects.filter(
        Q(email__icontains=terme) | Q(nom__icontains=terme) | Q(prenom__icontains=terme)
    ).order_by("nom")[:10]

    return Response([
        {
            "id_compte": c.id_compte,
            "email": c.email,
            "nom": c.nom,
            "prenom": c.prenom,
            "est_membre": hasattr(c, "membre"),
            "est_controleur": hasattr(c, "controleur"),
            "est_admin": c.est_admin,
        }
        for c in comptes
    ])


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_liste_controleurs(request):
    """GET /api/admin/controleurs/"""
    controleurs = Controleur.objects.select_related("compte", "evenement_assigne").order_by(
        "-date_nomination"
    )
    return Response([
        {
            "id_controleur": c.id_controleur,
            "nom": c.compte.nom,
            "prenom": c.compte.prenom,
            "email": c.compte.email,
            "zone_affectation": c.zone_affectation,
            "date_nomination": c.date_nomination,
            "statut_compte": c.compte.statut_compte,
            "est_aussi_membre": hasattr(c.compte, "membre"),
            "evenement_assigne": (
                {"id_evenement": c.evenement_assigne.id_evenement, "titre": c.evenement_assigne.titre}
                if c.evenement_assigne else None
            ),
        }
        for c in controleurs
    ])


@api_view(["PATCH"])
@permission_classes([EstAdmin])
def vue_modifier_controleur(request, id_controleur):
    """
    PATCH /api/admin/controleur/<id>/
    Corps : {"zone_affectation"?, "nom"?, "prenom"?, "email"?} (tous optionnels)
    """
    try:
        controleur = Controleur.objects.select_related("compte").get(
            id_controleur=id_controleur
        )
    except Controleur.DoesNotExist:
        return Response({"erreur": "Contrôleur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    zone = request.data.get("zone_affectation")
    if zone is not None:
        controleur.zone_affectation = zone
        controleur.save(update_fields=["zone_affectation"])

    champs_compte = ["nom", "prenom", "email"]
    maj_compte = [c for c in champs_compte if request.data.get(c)]
    if maj_compte:
        for champ in maj_compte:
            setattr(controleur.compte, champ, request.data[champ])
        controleur.compte.save(update_fields=maj_compte)

    JournalAudit.objects.create(
        type_action="modification_controleur",
        description=f"{controleur.compte.email} modifié (zone : {zone})",
        compte_auteur=request.user,
    )
    return Response({
        "id_controleur": controleur.id_controleur,
        "zone_affectation": controleur.zone_affectation,
        "nom": controleur.compte.nom,
        "prenom": controleur.compte.prenom,
        "email": controleur.compte.email,
    })


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_assigner_controleur(request, id_controleur):
    """
    POST /api/admin/controleur/<id>/assigner/
    Corps : {"id_evenement"}
    Affecte le contrôleur à un nouvel événement, lui redonnant le
    droit de scanner (voir la vérification dans vue_confirmer_entree).
    Ne touche jamais au statut du compte (voir vue_terminer_evenement).
    """
    try:
        controleur = Controleur.objects.select_related("compte").get(id_controleur=id_controleur)
    except Controleur.DoesNotExist:
        return Response({"erreur": "Contrôleur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    id_evenement = request.data.get("id_evenement")
    if not id_evenement:
        return Response({"erreur": "id_evenement requis"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        evenement = Evenement.objects.get(pk=id_evenement, est_termine=False)
    except Evenement.DoesNotExist:
        return Response(
            {"erreur": "Événement introuvable ou déjà terminé"}, status=status.HTTP_400_BAD_REQUEST
        )

    controleur.evenement_assigne = evenement
    controleur.save(update_fields=["evenement_assigne"])

    JournalAudit.objects.create(
        type_action="assignation_controleur",
        description=f"{controleur.compte.email} assigné à l'événement « {evenement.titre} »",
        compte_auteur=request.user,
    )
    return Response({
        "id_controleur": controleur.id_controleur,
        "evenement_assigne": {"id_evenement": evenement.id_evenement, "titre": evenement.titre},
    })


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_reinitialiser_mot_de_passe_controleur(request, id_controleur):
    """
    POST /api/admin/controleur/<id>/reinitialiser-mot-de-passe/
    Génère un mot de passe temporaire (renvoyé en clair UNE SEULE FOIS
    dans cette réponse, à communiquer au contrôleur) et force son
    changement à la prochaine connexion.
    """
    try:
        controleur = Controleur.objects.select_related("compte").get(id_controleur=id_controleur)
    except Controleur.DoesNotExist:
        return Response({"erreur": "Contrôleur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    mot_de_passe_temp = f"AP2A-{secrets.token_hex(4)}"
    controleur.compte.definir_mot_de_passe(mot_de_passe_temp)
    controleur.compte.doit_changer_mot_de_passe = True
    controleur.compte.save()

    JournalAudit.objects.create(
        type_action="reinitialisation_mot_de_passe_controleur",
        description=f"Mot de passe réinitialisé pour {controleur.compte.email}",
        compte_auteur=request.user,
    )
    return Response({"email": controleur.compte.email, "mot_de_passe_temporaire": mot_de_passe_temp})


@api_view(["DELETE"])
@permission_classes([EstAdmin])
def vue_supprimer_controleur(request, id_controleur):
    """
    DELETE /api/admin/controleur/<id>/
    Supprime UNIQUEMENT le rôle de contrôleur (la ligne Controleur),
    jamais le Compte associé - un contrôleur désigné parmi les
    membres reste membre après le retrait de ce rôle.
    """
    try:
        controleur = Controleur.objects.select_related("compte").get(
            id_controleur=id_controleur
        )
    except Controleur.DoesNotExist:
        return Response({"erreur": "Contrôleur introuvable"}, status=status.HTTP_404_NOT_FOUND)

    email = controleur.compte.email

    # CORRIGÉ : si ce contrôleur a déjà des scans enregistrés dans
    # PARTICIPE (contrainte PROTECT, posée volontairement lors du MLD
    # pour ne jamais casser la traçabilité d'un événement passé), la
    # suppression plantait silencieusement (erreur 500 brute, aucun
    # message côté Flutter). On l'intercepte pour renvoyer une
    # explication claire à la place.
    try:
        controleur.delete()
    except ProtectedError:
        return Response(
            {
                "erreur": (
                    "Impossible de retirer ce contrôleur : il a déjà des "
                    "entrées de présence enregistrées à son nom. Retirer "
                    "son rôle casserait la traçabilité de ces événements."
                )
            },
            status=status.HTTP_409_CONFLICT,
        )

    JournalAudit.objects.create(
        type_action="suppression_controleur",
        description=f"Rôle contrôleur retiré à {email}",
        compte_auteur=request.user,
    )
    return Response({"supprime": True})


@api_view(["POST"])
@permission_classes([EstAuthentifie])
def vue_changer_mot_de_passe(request):
    """
    POST /api/changer-mot-de-passe/
    Corps : {"ancien_mot_de_passe": "...", "nouveau_mot_de_passe": "..."}
    Accessible à N'IMPORTE QUEL compte connecté (membre, contrôleur,
    admin) - contrairement aux autres endpoints, pas de restriction
    de rôle : tout le monde doit pouvoir changer son propre mot de passe.
    """
    compte = request.user
    ancien = request.data.get("ancien_mot_de_passe")
    nouveau = request.data.get("nouveau_mot_de_passe")

    if not ancien or not nouveau:
        return Response(
            {"erreur": "ancien_mot_de_passe et nouveau_mot_de_passe sont requis"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not compte.verifier_mot_de_passe(ancien):
        return Response(
            {"erreur": "Ancien mot de passe incorrect"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if len(nouveau) < 8:
        return Response(
            {"erreur": "Le nouveau mot de passe doit faire au moins 8 caractères"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    compte.definir_mot_de_passe(nouveau)
    compte.doit_changer_mot_de_passe = False
    compte.save()

    return Response({"modifie": True})


@api_view(["GET"])
@permission_classes([EstAuthentifie])
def vue_mon_profil_compte(request):
    """
    GET /api/mon-profil/
    Profil de N'IMPORTE QUEL compte connecté (membre, contrôleur,
    admin) - remplace l'ancien écran "changer mot de passe" par un
    vrai écran Profil. La photo n'existe que pour un compte membre
    (le champ vit sur MEMBRE, pas sur COMPTE).
    """
    compte = request.user
    membre = getattr(compte, "membre", None)
    return Response({
        "nom": compte.nom,
        "prenom": compte.prenom,
        "email": compte.email,
        "telephone": compte.telephone,
        "est_membre": membre is not None,
        "photo": membre.photo.url if membre and membre.photo else None,
    })


@api_view(["PATCH"])
@permission_classes([EstAuthentifie])
def vue_modifier_profil_compte(request):
    """PATCH /api/mon-profil/ - Corps : {"nom", "prenom", "telephone"} (tous optionnels)"""
    compte = request.user
    donnees = request.data

    if donnees.get("nom"):
        compte.nom = donnees["nom"]
    if donnees.get("prenom"):
        compte.prenom = donnees["prenom"]
    if "telephone" in donnees:
        compte.telephone = donnees["telephone"] or None
    compte.save()

    return Response({"modifie": True})


@api_view(["POST"])
@permission_classes([EstMembre])
@parser_classes([MultiPartParser, FormParser])
def vue_televerser_photo(request):
    """
    POST /api/mon-profil/photo/
    Champ "photo" multipart. Réservé aux comptes membres (la photo vit
    sur MEMBRE) - mais profite AUSSI, sans rien faire de plus, à un
    éventuel badge Participant lié : vue_verifier_participant regarde
    en priorité la photo du membre lié avant celle du participant.
    """
    photo = request.FILES.get("photo")
    if photo is None:
        return Response({"erreur": "Fichier manquant (champ 'photo')"}, status=status.HTTP_400_BAD_REQUEST)

    membre = request.user.membre
    membre.photo = photo
    membre.save()
    return Response({"photo": membre.photo.url})


@api_view(["GET"])
@permission_classes([PeutControler])
def vue_liste_evenements(request):
    """
    GET /api/evenements/
    Événements EN COURS uniquement (est_termine=False), pour que le
    contrôleur/admin choisisse parmi ceux qu'il peut encore scanner -
    un événement terminé ne doit plus apparaître ici (décision prise
    ensemble).
    """
    evenements = Evenement.objects.filter(est_termine=False).order_by("-id_evenement")[:20]
    return Response([
        {
            "id_evenement": e.id_evenement,
            "titre": e.titre,
            "lieu": e.lieu,
            "type_evenement": e.type_evenement,
            "nombre_seances": e.seances.count(),
        }
        for e in evenements
    ])


@api_view(["GET"])
@permission_classes([EstMembre])
def vue_mon_profil(request):
    """
    GET /api/membre/moi/
    Profil complet du membre connecté + ses DEUX cartes indépendantes :
    la carte QR virtuelle (toujours disponible, générée automatiquement
    si absente - elle vit dans l'app, jamais affectée par la perte de
    la carte physique) et la carte NFC physique (remise en personne par
    l'association, peut être absente ou bloquée sans impact sur la QR).
    """
    membre = request.user.membre
    compte = request.user

    carte_qr = membre.cartes.filter(type_carte="QR", statut_carte="ACTIVE").first()
    if carte_qr is None:
        carte_qr = Carte.objects.create(
            uuid=str(uuid_lib.uuid4()),
            type_carte="QR",
            id_version_cle=settings.HMAC_VERSION_ACTIVE,
            membre=membre,
        )
    contenu_carte = reconstruire_contenu_carte(str(carte_qr.uuid), carte_qr.id_version_cle)

    carte_nfc = membre.cartes.filter(type_carte="NFC").order_by("-date_emission").first()

    return Response({
        "nom": compte.nom,
        "prenom": compte.prenom,
        "numero_adherent": membre.numero_adherent,
        "section": membre.section.nom_section,
        "date_adhesion": membre.date_adhesion,
        "statut_adhesion": membre.statut_adhesion,
        "photo": membre.photo.url if membre.photo else None,
        "carte_qr": {
            "statut_carte": carte_qr.statut_carte,
            "contenu_carte": contenu_carte,
        },
        "carte_physique": (
            {"type_carte": "NFC", "statut_carte": carte_nfc.statut_carte}
            if carte_nfc else None
        ),
    })


@api_view(["GET"])
@permission_classes([EstMembre])
def vue_mon_historique(request):
    """
    GET /api/membre/historique/
    Liste des événements auxquels le membre connecté a participé,
    du plus récent au plus ancien.
    """
    membre = request.user.membre
    participations = (
        Participe.objects.filter(membre=membre)
        .select_related("seance__evenement")
        .order_by("-heure_arrivee")
    )

    return Response([
        {
            "titre": p.seance.evenement.titre,
            "lieu": p.seance.evenement.lieu,
            "numero_seance": p.seance.numero_ordre,
            "date_seance": p.seance.date_seance,
            "heure_arrivee": p.heure_arrivee,
            "methode_scan": p.methode_scan,
        }
        for p in participations
    ])


@api_view(["GET"])
@permission_classes([EstControleur])
def vue_mon_historique_controleur(request):
    """
    GET /api/controleur/historique/
    Scans effectués PAR le contrôleur connecté (événements), les plus
    récents d'abord - pour son propre suivi dans le portail contrôleur.
    """
    controleur = request.user.controleur
    scans = (
        Participe.objects.filter(controleur_scan=controleur)
        .select_related("membre__compte", "seance__evenement")
        .order_by("-heure_arrivee")[:100]
    )
    return Response([
        {
            "membre": f"{p.membre.compte.prenom} {p.membre.compte.nom}",
            "numero_adherent": p.membre.numero_adherent,
            "evenement": p.seance.evenement.titre,
            "numero_seance": p.seance.numero_ordre,
            "heure_arrivee": p.heure_arrivee,
            "methode_scan": p.methode_scan,
        }
        for p in scans
    ])


@api_view(["GET"])
@permission_classes([AllowAny])
def vue_liste_sections(request):
    """
    GET /api/admin/sections/
    Rendue PUBLIQUE (AllowAny) : nécessaire pour le formulaire
    d'inscription, où une personne pas encore membre doit pouvoir
    choisir sa section. Le nom des sections n'a rien de sensible.
    """
    return Response([
        {"id_section": s.id_section, "nom_section": s.nom_section, "ville": s.ville}
        for s in Section.objects.order_by("nom_section")
    ])


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_liste_membres(request):
    """
    GET /api/admin/membres/?q=&statut_carte=&id_section=&fonction=&tri=

    q : recherche sur nom, prénom ou numéro d'adhérent
    statut_carte : ACTIVE / BLOQUEE / PERDUE
    id_section : filtre sur une section précise (optionnel, secondaire)
    fonction : filtre sur la fonction AP2A (champ d'identité principal)
    tri : "nom" / "date_adhesion" / "section" / "fonction" (défaut : -date_adhesion)
    """
    membres = (
        Membre.objects.select_related("compte", "section")
        .prefetch_related("cartes")
    )

    q = request.query_params.get("q", "").strip()
    if q:
        membres = membres.filter(
            Q(compte__nom__icontains=q)
            | Q(compte__prenom__icontains=q)
            | Q(numero_adherent__icontains=q)
        )

    id_section = request.query_params.get("id_section")
    if id_section:
        membres = membres.filter(section__id_section=id_section)

    fonction = request.query_params.get("fonction")
    if fonction:
        membres = membres.filter(fonction_association=fonction)

    tri = request.query_params.get("tri", "-date_adhesion")
    tri_valides = {
        "nom": "compte__nom",
        "date_adhesion": "date_adhesion",
        "-date_adhesion": "-date_adhesion",
        "section": "section__nom_section",
        "fonction": "fonction_association",
    }
    membres = membres.order_by(tri_valides.get(tri, "-date_adhesion"))

    statut_carte_filtre = request.query_params.get("statut_carte")

    resultat = []
    for m in membres:
        carte = m.cartes.order_by("-id_carte").first()
        statut_carte = carte.statut_carte if carte else None

        # Filtre sur le statut de carte : appliqué ici (après avoir
        # déterminé la carte la plus récente), car ce n'est pas un
        # champ direct de MEMBRE mais dérivé de sa carte.
        if statut_carte_filtre and statut_carte != statut_carte_filtre:
            continue

        resultat.append({
            "id_membre": m.id_membre,
            "nom": m.compte.nom,
            "prenom": m.compte.prenom,
            "numero_adherent": m.numero_adherent,
            "statut_adhesion": m.statut_adhesion,
            "fonction_association": m.fonction_association,
            "fonction_association_libelle": m.get_fonction_association_display(),
            "section": m.section.nom_section if m.section else None,
            "id_carte": carte.id_carte if carte else None,
            "statut_carte": statut_carte,
        })

    return Response(resultat)


def _serialiser_membre_detail(membre):
    cartes = membre.cartes.order_by("-date_emission")
    # BUG CORRIGÉ (même erreur que dans vue_liste_membres) : on ne
    # cherchait qu'une carte ACTIVE, donc une carte déjà bloquée/perdue
    # n'était jamais trouvée -> impossible de la réactiver depuis
    # l'interface. On prend la carte la plus récente, quel que soit
    # son statut.
    carte_courante = cartes.first()

    return {
        "id_membre": membre.id_membre,
        "nom": membre.compte.nom,
        "prenom": membre.compte.prenom,
        "email": membre.compte.email,
        "telephone": membre.compte.telephone,
        "numero_adherent": membre.numero_adherent,
        "fonction_association": membre.fonction_association,
        "fonction_association_libelle": membre.get_fonction_association_display(),
        "section": membre.section.nom_section if membre.section else None,
        "id_section": membre.section.id_section if membre.section else None,
        "date_adhesion": membre.date_adhesion,
        "statut_adhesion": membre.statut_adhesion,
        "est_admin": membre.compte.est_admin,
        "est_admin_principal": membre.compte.est_admin and membre.compte.compte_nommant_id is None,
        "est_controleur": hasattr(membre.compte, "controleur"),
        "id_carte_active": carte_courante.id_carte if carte_courante else None,
        "statut_carte": carte_courante.statut_carte if carte_courante else None,
        "nombre_participations": Participe.objects.filter(membre=membre).count(),
        "historique_cartes": [
            {
                "id_carte": c.id_carte,
                "type_carte": c.type_carte,
                "statut_carte": c.statut_carte,
                "date_emission": c.date_emission,
            }
            for c in cartes
        ],
    }


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_detail_membre(request, id_membre):
    """
    GET /api/admin/membre/<id_membre>/
    Fiche complète d'un membre : profil, carte active, historique
    des cartes (pour voir les pertes passées), nombre d'événements.
    """
    try:
        membre = Membre.objects.select_related("compte", "section").get(
            id_membre=id_membre
        )
    except Membre.DoesNotExist:
        return Response({"erreur": "Membre introuvable"}, status=status.HTTP_404_NOT_FOUND)

    return Response(_serialiser_membre_detail(membre))


@api_view(["PATCH"])
@permission_classes([EstAdmin])
def vue_modifier_membre(request, id_membre):
    """
    PATCH /api/admin/membre/<id_membre>/modifier/
    Met à jour la fiche d'un membre : identité et coordonnées du compte
    lié (nom, prénom, email, téléphone), section et statut d'adhésion.
    Tous les champs sont optionnels (mise à jour partielle : seuls les
    champs présents dans le corps de la requête sont modifiés).
    """
    from .models import Section

    try:
        membre = Membre.objects.select_related("compte", "section").get(id_membre=id_membre)
    except Membre.DoesNotExist:
        return Response({"erreur": "Membre introuvable"}, status=status.HTTP_404_NOT_FOUND)

    donnees = request.data
    compte = membre.compte

    if "email" in donnees:
        email = (donnees.get("email") or "").strip()
        if not email:
            return Response({"erreur": "L'email ne peut pas être vide"}, status=status.HTTP_400_BAD_REQUEST)
        if Compte.objects.exclude(pk=compte.pk).filter(email__iexact=email).exists():
            return Response(
                {"erreur": "Cet email est déjà utilisé par un autre compte"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        compte.email = email

    if "nom" in donnees:
        nom = (donnees.get("nom") or "").strip()
        if not nom:
            return Response({"erreur": "Le nom ne peut pas être vide"}, status=status.HTTP_400_BAD_REQUEST)
        compte.nom = nom

    if "prenom" in donnees:
        prenom = (donnees.get("prenom") or "").strip()
        if not prenom:
            return Response({"erreur": "Le prénom ne peut pas être vide"}, status=status.HTTP_400_BAD_REQUEST)
        compte.prenom = prenom

    if "telephone" in donnees:
        compte.telephone = (donnees.get("telephone") or "").strip() or None

    compte.save()

    if "id_section" in donnees:
        id_section = donnees["id_section"]
        if not id_section:
            membre.section = None
        else:
            try:
                membre.section = Section.objects.get(pk=id_section)
            except Section.DoesNotExist:
                return Response({"erreur": "Section introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if "fonction_association" in donnees:
        fonction = donnees.get("fonction_association")
        if fonction not in dict(Membre.FONCTION_CHOICES):
            return Response({"erreur": "Fonction AP2A invalide"}, status=status.HTTP_400_BAD_REQUEST)
        membre.fonction_association = fonction

    if "statut_adhesion" in donnees:
        statut = donnees.get("statut_adhesion")
        if statut not in dict(Membre.STATUT_CHOICES):
            return Response({"erreur": "Statut d'adhésion invalide"}, status=status.HTTP_400_BAD_REQUEST)
        membre.statut_adhesion = statut

    membre.save()

    JournalAudit.objects.create(
        type_action="MODIFICATION_MEMBRE",
        description=f"Fiche modifiée : {compte.prenom} {compte.nom} ({membre.numero_adherent})",
        compte_auteur=request.user,
    )

    return Response(_serialiser_membre_detail(membre))


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_terminer_evenement(request, id_evenement):
    """
    POST /api/admin/evenement/<id_evenement>/terminer/
    Réservé à l'admin principal OU à l'admin qui a organisé CET
    événement précis (règle donnée : ni les autres admins, ni les
    contrôleurs ne peuvent le faire). Une fois terminé, l'événement
    disparaît de la liste de sélection et refuse tout nouveau scan.
    """
    try:
        evenement = Evenement.objects.select_related("compte_organisateur").get(
            id_evenement=id_evenement
        )
    except Evenement.DoesNotExist:
        return Response({"erreur": "Événement introuvable"}, status=status.HTTP_404_NOT_FOUND)

    est_admin_principal = request.user.est_admin and request.user.compte_nommant_id is None
    est_organisateur = evenement.compte_organisateur_id == request.user.id_compte

    if not (est_admin_principal or est_organisateur):
        return Response(
            {"erreur": "Seul l'admin principal ou l'organisateur peut terminer cet événement"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if evenement.est_termine:
        return Response({"erreur": "Cet événement est déjà terminé"}, status=status.HTTP_400_BAD_REQUEST)

    evenement.est_termine = True
    evenement.date_fin = timezone.now()
    evenement.save()

    # Les contrôleurs assignés à CET événement perdent leur assignation
    # (donc leur droit de scanner - voir la vérification dans
    # vue_confirmer_entree) sans que leur COMPTE soit suspendu : un
    # contrôleur est souvent aussi membre (voire admin) par ailleurs,
    # et une suspension de compte bloquerait à tort ces autres accès.
    # Un admin les réaffecte ensuite à un nouvel événement pour leur
    # redonner le droit de scanner (voir vue_assigner_controleur).
    Controleur.objects.filter(evenement_assigne=evenement).update(evenement_assigne=None)

    JournalAudit.objects.create(
        type_action="fin_evenement",
        description=f"Événement '{evenement.titre}' marqué comme terminé",
        compte_auteur=request.user,
    )
    return Response({"id_evenement": evenement.id_evenement, "est_termine": True})


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_historique_evenements(request):
    """
    GET /api/admin/evenements/historique/
    TOUS les événements (en cours + terminés + annulés).
    """
    evenements = Evenement.objects.select_related("compte_organisateur").order_by(
        "-id_evenement"
    )
    return Response([
        {
            "id_evenement": e.id_evenement,
            "titre": e.titre,
            "lieu": e.lieu,
            "type_evenement": e.type_evenement,
            "description": e.description,
            "mode_inscription": e.mode_inscription,
            "capacite_max": e.capacite_max,
            "est_termine": e.est_termine,
            "est_annule": e.est_annule,
            "nombre_seances": e.seances.count(),
            "nombre_participants": Participe.objects.filter(seance__evenement=e).count(),
        }
        for e in evenements
    ])


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_detail_evenement(request, id_evenement):
    """
    GET /api/admin/evenement/<id_evenement>/
    Statistiques complètes d'un événement, agrégées sur TOUTES ses
    séances, avec un détail séance par séance en plus du total.
    """
    try:
        evenement = Evenement.objects.select_related("compte_organisateur").get(
            id_evenement=id_evenement
        )
    except Evenement.DoesNotExist:
        return Response({"erreur": "Événement introuvable"}, status=status.HTTP_404_NOT_FOUND)

    participations = (
        Participe.objects.filter(seance__evenement=evenement)
        .select_related("membre__compte", "controleur_scan__compte", "seance")
        .order_by("-heure_arrivee")
    )

    repartition_methode = {}
    for p in participations:
        repartition_methode[p.methode_scan] = repartition_methode.get(p.methode_scan, 0) + 1

    # Répartition par séance : utile pour voir la fréquentation
    # jour par jour d'une formation multi-jours.
    repartition_seances = {}
    for p in participations:
        cle = f"Séance {p.seance.numero_ordre}"
        repartition_seances[cle] = repartition_seances.get(cle, 0) + 1

    return Response({
        "id_evenement": evenement.id_evenement,
        "titre": evenement.titre,
        "lieu": evenement.lieu,
        "type_evenement": evenement.type_evenement,
        "description": evenement.description,
        "mode_inscription": evenement.mode_inscription,
        "capacite_max": evenement.capacite_max,
        "est_termine": evenement.est_termine,
        "est_annule": evenement.est_annule,
        "date_fin": evenement.date_fin,
        "nombre_seances": evenement.seances.count(),
        "organisateur": f"{evenement.compte_organisateur.prenom} {evenement.compte_organisateur.nom}",
        "total_participants": participations.count(),
        "repartition_methode": repartition_methode,
        "repartition_seances": repartition_seances,
        "participants": [
            {
                "nom": p.membre.compte.nom,
                "prenom": p.membre.compte.prenom,
                "numero_adherent": p.membre.numero_adherent,
                "seance": p.seance.numero_ordre,
                "heure_arrivee": p.heure_arrivee,
                "methode_scan": p.methode_scan,
                "controleur": f"{p.controleur_scan.compte.prenom} {p.controleur_scan.compte.nom}",
            }
            for p in participations
        ],
    })


@api_view(["POST"])
@permission_classes([EstAdminPrincipal])
def vue_destituer_admin(request, id_compte):
    """
    POST /api/admin/destituer-admin/<id_compte>/
    Réservé à l'admin principal. Impossible de destituer l'admin
    principal lui-même : son statut n'est pas un simple drapeau qu'on
    retire, il est défini structurellement par l'absence de nommant
    (voir EstAdminPrincipal) - le retirer laisserait le système sans
    admin principal du tout.
    """
    try:
        compte_cible = Compte.objects.get(id_compte=id_compte)
    except Compte.DoesNotExist:
        return Response({"erreur": "Compte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if compte_cible.compte_nommant_id is None:
        return Response(
            {"erreur": "Impossible de destituer l'admin principal"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not compte_cible.est_admin:
        return Response({"erreur": "Ce compte n'est pas admin"}, status=status.HTTP_400_BAD_REQUEST)

    compte_cible.est_admin = False
    compte_cible.save()

    JournalAudit.objects.create(
        type_action="destitution_admin",
        description=f"{compte_cible.email} destitué du rôle admin",
        compte_auteur=request.user,
    )
    return Response({"email": compte_cible.email, "est_admin": False})


@api_view(["GET"])
@permission_classes([EstAdminPrincipal])
def vue_liste_admins(request):
    """
    GET /api/admin/liste-admins/
    Réservé à l'admin principal (seul habilité à destituer). Liste
    tous les comptes admin, avec un indicateur pour ne jamais proposer
    de destituer l'admin principal lui-même.
    """
    admins = Compte.objects.filter(est_admin=True).order_by("nom")
    return Response([
        {
            "id_compte": a.id_compte,
            "nom": a.nom,
            "prenom": a.prenom,
            "email": a.email,
            "est_admin_principal": a.compte_nommant_id is None,
        }
        for a in admins
    ])


# =========================================================================
# EXPORT DE RAPPORTS (Excel / PDF)
# =========================================================================

def _donnees_rapport_statistiques():
    """Prépare (titre, en-têtes, lignes) pour le rapport 'statistiques'."""
    par_section = (
        Membre.objects.values("section__nom_section")
        .annotate(nombre=Count("id_membre"))
        .order_by("-nombre")
    )
    entetes = ["Section", "Nombre de membres"]
    lignes = [[r["section__nom_section"] or "Sans section", r["nombre"]] for r in par_section]
    lignes.append(["TOTAL", Membre.objects.count()])
    lignes.append(["Dont actifs", Membre.objects.filter(statut_adhesion="ACTIF").count()])
    return "Statistiques globales", entetes, lignes


def _donnees_rapport_evenement(id_evenement):
    """Prépare (titre, en-têtes, lignes) pour le rapport 'événement'."""
    evenement = Evenement.objects.get(id_evenement=id_evenement)
    participations = (
        Participe.objects.filter(seance__evenement=evenement)
        .select_related("membre__compte", "seance", "controleur_scan__compte")
        .order_by("seance__numero_ordre", "heure_arrivee")
    )
    entetes = ["Séance", "Nom", "Prénom", "N° adhérent", "Heure d'arrivée", "Méthode", "Contrôleur"]
    lignes = [
        [
            p.seance.numero_ordre,
            p.membre.compte.nom,
            p.membre.compte.prenom,
            p.membre.numero_adherent,
            p.heure_arrivee.strftime("%d/%m/%Y %H:%M"),
            p.methode_scan,
            f"{p.controleur_scan.compte.prenom} {p.controleur_scan.compte.nom}",
        ]
        for p in participations
    ]
    titre = f"Rapport — {evenement.titre}"
    return titre, entetes, lignes


def _scans_controle_acces(request):
    """
    Scans consolidés (événements + cohortes de formation), filtrables
    par contrôleur/date - source commune pour la vue JSON en ligne et
    l'export. Retourne une liste de dicts triée par heure décroissante.
    """
    id_controleur = request.query_params.get("id_controleur")
    date_debut = request.query_params.get("date_debut")
    date_fin = request.query_params.get("date_fin")

    participations = Participe.objects.select_related(
        "membre__compte", "seance__evenement", "controleur_scan__compte"
    )
    presences = PresenceCohorte.objects.select_related(
        "participant", "seance_cohorte__cohorte", "controleur_scan__compte"
    )
    if id_controleur:
        participations = participations.filter(controleur_scan_id=id_controleur)
        presences = presences.filter(controleur_scan_id=id_controleur)
    if date_debut:
        participations = participations.filter(heure_arrivee__date__gte=date_debut)
        presences = presences.filter(heure_arrivee__date__gte=date_debut)
    if date_fin:
        participations = participations.filter(heure_arrivee__date__lte=date_fin)
        presences = presences.filter(heure_arrivee__date__lte=date_fin)

    scans = [
        {
            "type": "evenement",
            "personne": f"{p.membre.compte.prenom} {p.membre.compte.nom}",
            "contexte": p.seance.evenement.titre,
            "controleur": f"{p.controleur_scan.compte.prenom} {p.controleur_scan.compte.nom}",
            "methode_scan": p.methode_scan,
            "heure_arrivee": p.heure_arrivee,
        }
        for p in participations
    ] + [
        {
            "type": "formation",
            "personne": f"{pr.participant.prenom} {pr.participant.nom}",
            "contexte": pr.seance_cohorte.cohorte.code_cohorte,
            "controleur": f"{pr.controleur_scan.compte.prenom} {pr.controleur_scan.compte.nom}",
            "methode_scan": pr.methode_scan,
            "heure_arrivee": pr.heure_arrivee,
        }
        for pr in presences
    ]
    scans.sort(key=lambda s: s["heure_arrivee"], reverse=True)
    return scans


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_rapport_controle_acces(request):
    """
    GET /api/admin/rapport-controle-acces/?id_controleur=&date_debut=&date_fin=
    Liste consolidée des scans (événements + formations) pour audit.
    """
    return Response(_scans_controle_acces(request)[:500])


def _donnees_rapport_controle_acces(request):
    """Prépare (titre, en-têtes, lignes) pour l'export du rapport 'controle_acces'."""
    scans = _scans_controle_acces(request)
    entetes = ["Type", "Personne", "Contexte", "Contrôleur", "Méthode", "Heure"]
    lignes = [
        [
            "Événement" if s["type"] == "evenement" else "Formation",
            s["personne"],
            s["contexte"],
            s["controleur"],
            s["methode_scan"],
            s["heure_arrivee"].strftime("%d/%m/%Y %H:%M"),
        ]
        for s in scans
    ]
    return "Rapport de contrôle d'accès", entetes, lignes


def _donnees_rapport_journal(request):
    """Prépare (titre, en-têtes, lignes) pour le rapport 'journal', avec les mêmes filtres que la consultation en ligne."""
    entrees = JournalAudit.objects.select_related("compte_auteur").order_by("-date_action")

    type_action = request.query_params.get("type_action")
    if type_action:
        entrees = entrees.filter(type_action=type_action)
    date_debut = request.query_params.get("date_debut")
    if date_debut:
        entrees = entrees.filter(date_action__date__gte=date_debut)
    date_fin = request.query_params.get("date_fin")
    if date_fin:
        entrees = entrees.filter(date_action__date__lte=date_fin)

    entetes = ["Date", "Type d'action", "Description", "Auteur"]
    lignes = [
        [
            e.date_action.strftime("%d/%m/%Y %H:%M"),
            e.type_action,
            e.description or "",
            e.compte_auteur.email,
        ]
        for e in entrees
    ]
    return "Journal d'audit", entetes, lignes


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_exporter_rapport(request):
    """
    GET /api/admin/rapports/export/?type=statistiques|evenement|journal
        &format=excel|pdf&id_evenement=...&type_action=...&date_debut=...&date_fin=...

    Renvoie directement un fichier téléchargeable (pas du JSON), d'où
    l'utilisation de HttpResponse plutôt que Response/DRF standard.
    """
    type_rapport = request.query_params.get("type")
    format_export = request.query_params.get("format", "excel")

    if type_rapport == "statistiques":
        titre, entetes, lignes = _donnees_rapport_statistiques()
    elif type_rapport == "evenement":
        id_evenement = request.query_params.get("id_evenement")
        if not id_evenement:
            return Response({"erreur": "id_evenement requis pour ce type de rapport"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            titre, entetes, lignes = _donnees_rapport_evenement(id_evenement)
        except Evenement.DoesNotExist:
            return Response({"erreur": "Événement introuvable"}, status=status.HTTP_404_NOT_FOUND)
    elif type_rapport == "journal":
        titre, entetes, lignes = _donnees_rapport_journal(request)
    elif type_rapport == "controle_acces":
        titre, entetes, lignes = _donnees_rapport_controle_acces(request)
    else:
        return Response(
            {"erreur": "type doit être 'statistiques', 'evenement', 'journal' ou 'controle_acces'"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if format_export == "pdf":
        tampon = pdf_depuis_tableau(titre, entetes, lignes)
        type_contenu = "application/pdf"
        extension = "pdf"
    else:
        tampon = excel_depuis_tableau(titre, entetes, lignes)
        type_contenu = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        extension = "xlsx"

    reponse = HttpResponse(tampon.getvalue(), content_type=type_contenu)
    nom_fichier = f"rapport_{type_rapport}.{extension}"
    reponse["Content-Disposition"] = f'attachment; filename="{nom_fichier}"'
    return reponse


@api_view(["GET"])
@permission_classes([EstMembre])
def vue_qr_actuel(request):
    """
    GET /api/membre/qr-actuel/
    Renvoie le contenu ROTATIF (uuid.version.fenetre.signature) de la
    carte active du membre connecté, régénéré à chaque appel. Endpoint
    léger et dédié (pas tout le profil) car il est interrogé toutes
    les 10 secondes tant que l'écran de carte reste ouvert.
    """
    membre = request.user.membre
    # Toujours la carte QR spécifiquement (indépendante de la carte
    # physique NFC, qui peut être bloquée sans que ceci soit affecté).
    carte_qr = membre.cartes.filter(type_carte="QR", statut_carte="ACTIVE").first()

    if carte_qr is None:
        return Response({"erreur": "Aucune carte QR active"}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        "contenu_carte": construire_contenu_rotatif(str(carte_qr.uuid)),
    })


# =========================================================================
# FORMATION / COHORTE
# =========================================================================

@api_view(["GET"])
@permission_classes([EstAuthentifie])
def vue_liste_formations(request):
    """GET /api/formations/ - le catalogue, visible par tout compte connecté."""
    formations = Formation.objects.order_by("titre")
    return Response([
        {
            "id_formation": f.id_formation,
            "titre": f.titre,
            "code_reference": f.code_reference,
            "description": f.description,
            "domaine": f.domaine,
            "duree_heures": f.duree_heures,
            "prerequis": f.prerequis,
            "nombre_cohortes": f.cohortes.count(),
        }
        for f in formations
    ])


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_creer_formation(request):
    """
    POST /api/admin/formation/
    Corps : {"titre", "code_reference", "description", "domaine",
             "duree_heures", "prerequis"}
    Seul "titre" est obligatoire. La Formation est un pur catalogue
    (le concept du cours, indépendant de toute session) : ni statut ni
    public cible n'y figurent, ces deux notions vivent au niveau de
    chaque Cohorte (une même formation peut avoir des cohortes à des
    stades différents, pour des publics différents).
    """
    donnees = request.data
    if not donnees.get("titre"):
        return Response({"erreur": "titre requis"}, status=status.HTTP_400_BAD_REQUEST)

    if donnees.get("code_reference") and Formation.objects.filter(
        code_reference=donnees["code_reference"]
    ).exists():
        return Response({"erreur": "Ce code de référence existe déjà"}, status=status.HTTP_400_BAD_REQUEST)

    formation = Formation.objects.create(
        titre=donnees["titre"],
        code_reference=donnees.get("code_reference") or None,
        description=donnees.get("description"),
        domaine=donnees.get("domaine"),
        duree_heures=donnees.get("duree_heures"),
        prerequis=donnees.get("prerequis"),
    )
    return Response(
        {"id_formation": formation.id_formation, "titre": formation.titre},
        status=status.HTTP_201_CREATED,
    )


@api_view(["PATCH", "DELETE"])
@permission_classes([EstAdmin])
def vue_modifier_ou_supprimer_formation(request, id_formation):
    """
    PATCH /api/admin/formation/<id>/  - modification partielle (seuls
        les champs présents dans le corps sont mis à jour)
    DELETE /api/admin/formation/<id>/ - refusé (409, message clair) si
        une cohorte de cette formation est en cours ou terminée (une
        session déjà déroulée n'est plus un brouillon qu'on peut effacer -
        même règle que la suppression d'une cohorte seule).
    """
    try:
        formation = Formation.objects.get(id_formation=id_formation)
    except Formation.DoesNotExist:
        return Response({"erreur": "Formation introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        synchroniser_statuts_cohortes()
        if formation.cohortes.filter(statut__in=["EN_COURS", "TERMINEE"]).exists():
            return Response(
                {
                    "erreur": (
                        "Impossible de supprimer cette formation : elle a au moins une cohorte en cours "
                        "ou terminée (session déjà déroulée)."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        titre = formation.titre
        # Supprime les cohortes restantes (encore en brouillon/programmée/annulée,
        # donc jamais déroulées) puis la formation elle-même.
        formation.cohortes.all().delete()
        formation.delete()
        JournalAudit.objects.create(
            type_action="suppression_formation",
            description=f"Formation supprimée : {titre}",
            compte_auteur=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # PATCH
    donnees = request.data
    if "code_reference" in donnees and donnees["code_reference"]:
        if Formation.objects.filter(
            code_reference=donnees["code_reference"]
        ).exclude(id_formation=id_formation).exists():
            return Response({"erreur": "Ce code de référence existe déjà"}, status=status.HTTP_400_BAD_REQUEST)

    champs_modifiables = [
        "titre", "code_reference", "description", "domaine",
        "duree_heures", "prerequis",
    ]
    for champ in champs_modifiables:
        if champ in donnees:
            setattr(formation, champ, donnees[champ])
    formation.save()

    JournalAudit.objects.create(
        type_action="modification_formation",
        description=f"Formation modifiée : {formation.titre}",
        compte_auteur=request.user,
    )

    return Response({"id_formation": formation.id_formation, "titre": formation.titre})


@api_view(["GET"])
@permission_classes([EstAuthentifie])
def vue_liste_cohortes(request, id_formation):
    """GET /api/formation/<id_formation>/cohortes/ - les cohortes d'une formation, ouvertes en priorité."""
    synchroniser_statuts_cohortes()
    cohortes = Cohorte.objects.filter(formation_id=id_formation).order_by("-date_debut")
    return Response([
        {
            "id_cohorte": c.id_cohorte,
            "code_cohorte": c.code_cohorte,
            "date_debut": c.date_debut,
            "date_fin": c.date_fin,
            "lieu": c.lieu,
            "formateur": c.formateur,
            "statut": c.statut,
            "nombre_inscrits": c.inscriptions.count(),
            "capacite_max": c.capacite_max,
            "est_payante": c.est_payante,
            "prix": str(c.prix) if c.prix is not None else None,
            "date_limite_inscription": c.date_limite_inscription,
            "seuil_certification": c.seuil_certification,
            "public_cible_type": c.public_cible_type,
        }
        for c in cohortes
    ])


def _recalculer_dates_cohorte(cohorte):
    """
    Recalcule date_debut/date_fin d'une cohorte à partir de ses séances
    RÉELLES (min/max des date_seance), pour qu'il soit impossible que ces
    deux champs divergent de ce qui est vraiment programmé. C'est ce
    calendrier qui pilote le passage automatique de statut (voir
    synchroniser_statuts_cohortes dans utils.py) - un admin qui ajoute une
    séance pour "aujourd'hui" doit voir sa cohorte basculer en cours,
    pas rester bloquée sur des dates saisies séparément et jamais mises
    à jour.

    Ne fait rien si la cohorte n'a plus aucune séance (on ne met pas
    date_debut/date_fin à NULL, ce sont des champs obligatoires).
    """
    bornes = cohorte.seances.aggregate(debut=Min("date_seance"), fin=Max("date_seance"))
    if bornes["debut"] is None:
        return
    nouveau_debut = timezone.localtime(bornes["debut"]).date()
    nouveau_fin = timezone.localtime(bornes["fin"]).date()
    if nouveau_debut != cohorte.date_debut or nouveau_fin != cohorte.date_fin:
        cohorte.date_debut = nouveau_debut
        cohorte.date_fin = nouveau_fin
        cohorte.save(update_fields=["date_debut", "date_fin"])


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_creer_cohorte(request):
    """
    POST /api/admin/cohorte/
    Corps : {"id_formation", "code_cohorte", "lieu", "formateur", "seances": [
                 {"date_seance": iso, "heure_fin": iso (optionnel),
                  "lieu": str (optionnel, sinon lieu de la cohorte),
                  "titre_seance": str (optionnel),
                  "type_seance": "COURS"|"TD"|"TP"|"EXAMEN",
                  "formateur_seance": str (optionnel)},
                 ...
             ]}
    PAS de "date_debut"/"date_fin" en entrée : ces deux champs sont
    calculés automatiquement à partir des séances fournies (min/max),
    pour ne jamais pouvoir diverger de ce qui est réellement programmé
    (voir _recalculer_dates_cohorte).
    """
    donnees = request.data
    champs_requis = ["id_formation", "code_cohorte", "lieu", "seances"]
    if not all(donnees.get(c) for c in champs_requis):
        return Response(
            {"erreur": f"Champs requis : {', '.join(champs_requis)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        formation = Formation.objects.get(id_formation=donnees["id_formation"])
    except Formation.DoesNotExist:
        return Response({"erreur": "Formation introuvable"}, status=status.HTTP_404_NOT_FOUND)

    seances_donnees = donnees["seances"]
    if not isinstance(seances_donnees, list) or len(seances_donnees) == 0:
        return Response(
            {"erreur": "'seances' doit être une liste non vide"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    dates_seances = []
    for s in seances_donnees:
        if not isinstance(s, dict) or not s.get("date_seance"):
            continue
        dt = parse_datetime(s["date_seance"])
        if dt is None:
            continue
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        dates_seances.append(dt)

    if not dates_seances:
        return Response(
            {"erreur": "Aucune séance valide fournie (date_seance manquante ou illisible)"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if Cohorte.objects.filter(code_cohorte=donnees["code_cohorte"]).exists():
        return Response({"erreur": "Ce code de cohorte existe déjà"}, status=status.HTTP_400_BAD_REQUEST)

    public_cible_type = donnees.get("public_cible_type", "TOUS")
    ids_membres_cibles = donnees.get("membres_cibles") or []
    if public_cible_type == "SPECIFIQUE" and not ids_membres_cibles:
        return Response(
            {"erreur": "Sélectionnez au moins un membre pour un public cible spécifique"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cohorte = Cohorte.objects.create(
        formation=formation,
        code_cohorte=donnees["code_cohorte"],
        date_debut=timezone.localtime(min(dates_seances)).date(),
        date_fin=timezone.localtime(max(dates_seances)).date(),
        lieu=donnees["lieu"],
        formateur=donnees.get("formateur"),
        capacite_max=donnees.get("capacite_max"),
        capacite_min=donnees.get("capacite_min"),
        materiel_necessaire=donnees.get("materiel_necessaire"),
        est_payante=donnees.get("est_payante", True),
        prix=donnees.get("prix"),
        prix_adherent=donnees.get("prix_adherent"),
        date_limite_inscription=donnees.get("date_limite_inscription"),
        conditions_annulation=donnees.get("conditions_annulation"),
        financeur=donnees.get("financeur"),
        numero_convention=donnees.get("numero_convention"),
        public_cible_type=public_cible_type,
    )
    if public_cible_type == "SPECIFIQUE":
        cohorte.membres_cibles.set(Membre.objects.filter(id_membre__in=ids_membres_cibles))

    for numero, s in enumerate(seances_donnees, start=1):
        if not isinstance(s, dict) or not s.get("date_seance"):
            continue  # ligne invalide, on l'ignore plutôt que de tout annuler
        SeanceCohorte.objects.create(
            cohorte=cohorte,
            numero_ordre=numero,
            date_seance=s["date_seance"],
            heure_fin=s.get("heure_fin"),
            lieu=s.get("lieu"),
            titre_seance=s.get("titre_seance"),
            type_seance=s.get("type_seance", "COURS"),
            formateur_seance=s.get("formateur_seance"),
        )

    return Response(
        {"id_cohorte": cohorte.id_cohorte, "code_cohorte": cohorte.code_cohorte},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_changer_statut_cohorte(request, id_cohorte):
    """
    POST /api/admin/cohorte/<id>/statut/ - Corps : {"statut": "..."}
    Réservé aux transitions qui n'ont pas déjà leur propre bouton dédié
    (typiquement ANNULEE) : seules les transitions logiques du cycle de
    vie sont acceptées (voir TRANSITIONS_COHORTE_AUTORISEES) - impossible
    de revenir en arrière ou de sauter une étape.
    """
    synchroniser_statuts_cohortes()
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    statut = request.data.get("statut")
    if statut not in dict(Cohorte.STATUT_CHOICES):
        return Response({"erreur": "Statut invalide"}, status=status.HTTP_400_BAD_REQUEST)

    if statut not in TRANSITIONS_COHORTE_AUTORISEES.get(cohorte.statut, set()):
        return Response(
            {"erreur": f"Impossible de passer de « {cohorte.statut} » à « {statut} »"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cohorte.statut = statut
    cohorte.save()
    return Response({"id_cohorte": cohorte.id_cohorte, "statut": cohorte.statut})


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_terminer_cohorte(request, id_cohorte):
    """
    POST /api/admin/cohorte/<id>/terminer/
    Bouton "Terminer" : clôture manuelle immédiate d'une cohorte EN_COURS,
    sans attendre sa date de fin programmée (voir aussi la clôture
    automatique dans synchroniser_statuts_cohortes).
    """
    synchroniser_statuts_cohortes()
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if cohorte.statut != "EN_COURS":
        return Response(
            {"erreur": "Seule une cohorte en cours peut être terminée manuellement"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cohorte.statut = "TERMINEE"
    cohorte.save()

    JournalAudit.objects.create(
        type_action="cloture_cohorte",
        description=f"Cohorte {cohorte.code_cohorte} clôturée manuellement",
        compte_auteur=request.user,
    )
    return Response({"id_cohorte": cohorte.id_cohorte, "statut": cohorte.statut})


@api_view(["PATCH", "DELETE"])
@permission_classes([EstAdmin])
def vue_modifier_ou_supprimer_cohorte(request, id_cohorte):
    """
    PATCH /api/admin/cohorte/<id>/  - modification partielle (lieu,
        formateur, capacité, tarifs... hors planning de séances)
    DELETE /api/admin/cohorte/<id>/ - suppression complète en CASCADE
        (séances, inscriptions, présences) - décision : contrairement
        à une formation (protégée tant qu'elle a des cohortes), une
        cohorte se supprime réellement, ex: session créée par erreur.
    """
    synchroniser_statuts_cohortes()
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        # Une cohorte en cours ou terminée correspond à des cours déjà
        # dispensés (présences, historique réel) - contrairement à une
        # cohorte encore en brouillon/programmée/annulée créée par erreur,
        # elle ne se supprime plus, seulement les statuts d'avant.
        if cohorte.statut in ("EN_COURS", "TERMINEE"):
            return Response(
                {"erreur": "Une cohorte en cours ou terminée ne peut plus être supprimée."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        code = cohorte.code_cohorte
        cohorte.delete()
        JournalAudit.objects.create(
            type_action="suppression_cohorte",
            description=f"Cohorte supprimée : {code}",
            compte_auteur=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    erreur = erreur_si_cohorte_verrouillee(cohorte)
    if erreur:
        return erreur

    # PATCH
    donnees = request.data
    champs_modifiables = [
        "code_cohorte", "date_debut", "date_fin", "lieu", "formateur",
        "capacite_max", "capacite_min", "materiel_necessaire",
        "est_payante", "prix", "prix_adherent", "date_limite_inscription",
        "conditions_annulation", "financeur", "numero_convention",
        "public_cible_type",
    ]
    if "code_cohorte" in donnees and donnees["code_cohorte"]:
        if Cohorte.objects.filter(
            code_cohorte=donnees["code_cohorte"]
        ).exclude(id_cohorte=id_cohorte).exists():
            return Response({"erreur": "Ce code de cohorte existe déjà"}, status=status.HTTP_400_BAD_REQUEST)

    for champ in champs_modifiables:
        if champ in donnees:
            setattr(cohorte, champ, donnees[champ])
    cohorte.save()

    if "membres_cibles" in donnees:
        cohorte.membres_cibles.set(Membre.objects.filter(id_membre__in=donnees["membres_cibles"]))

    return Response({"id_cohorte": cohorte.id_cohorte, "code_cohorte": cohorte.code_cohorte})


@api_view(["POST"])
@permission_classes([EstMembre])
def vue_sinscrire_cohorte(request, id_cohorte):
    """
    POST /api/cohorte/<id>/inscription/
    Auto-inscription par le membre connecté. RAPPROCHEMENT AUTOMATIQUE :
    si un Participant "orphelin" (sans compte lié) existe déjà QUELQUE
    PART dans le système avec le même numéro de téléphone (import
    Excel/manuel sur N'IMPORTE QUELLE formation antérieure), on
    réutilise cette identité et son badge plutôt que d'en créer un
    nouveau - c'est ce qui garantit un seul badge par personne, pour
    toutes ses formations.
    """
    synchroniser_statuts_cohortes()
    compte = request.user
    membre = compte.membre
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if cohorte.statut in ("TERMINEE", "ANNULEE"):
        return Response(
            {"erreur": "Cette cohorte est terminée ou annulée, inscription impossible"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Le participant existe-t-il déjà pour ce membre (peu importe la
    # cohorte) ? Un membre a AU PLUS un participant lié (OneToOne).
    participant = getattr(membre, "participant_externe", None)

    if participant is None and compte.telephone:
        # Rapprochement : un participant orphelin avec ce téléphone
        # existe-t-il déjà (créé par Excel/manuel sur une autre
        # formation) ?
        participant = Participant.objects.filter(
            membre__isnull=True, telephone=compte.telephone
        ).first()
        if participant:
            participant.membre = membre
            participant.save()

    if participant is None:
        # Aucune identité existante à récupérer : on en crée une
        # nouvelle, avec son badge, liée directement à ce membre.
        nouvel_uuid = uuid_lib.uuid4()
        _, version = generer_signature(str(nouvel_uuid))
        participant = Participant.objects.create(
            nom=compte.nom,
            prenom=compte.prenom,
            telephone=compte.telephone,
            uuid=nouvel_uuid,
            id_version_cle=version,
            membre=membre,
        )

    try:
        InscriptionCohorte.objects.create(participant=participant, cohorte=cohorte, source="COMPTE")
    except IntegrityError:
        return Response(
            {"erreur": "Vous êtes déjà inscrit à cette cohorte"},
            status=status.HTTP_409_CONFLICT,
        )

    return Response({"inscrit": True, "cohorte": cohorte.code_cohorte}, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([EstMembre])
def vue_mes_cohortes(request):
    """GET /api/membre/mes-cohortes/ - les cohortes auxquelles le membre connecté est inscrit."""
    synchroniser_statuts_cohortes()
    membre = request.user.membre
    participant = getattr(membre, "participant_externe", None)
    if participant is None:
        return Response([])

    inscriptions = (
        InscriptionCohorte.objects.filter(participant=participant)
        .select_related("cohorte__formation")
        .order_by("-date_inscription")
    )
    return Response([
        {
            "id_cohorte": i.cohorte.id_cohorte,
            "code_cohorte": i.cohorte.code_cohorte,
            "formation": i.cohorte.formation.titre,
            "date_debut": i.cohorte.date_debut,
            "date_fin": i.cohorte.date_fin,
            "statut_cohorte": i.cohorte.statut,
            "statut_inscription": i.statut,
        }
        for i in inscriptions
    ])


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_liste_participants_cohorte(request, id_cohorte):
    """
    GET /api/admin/cohorte/<id>/participants/
    Liste des personnes inscrites à CETTE cohorte (via InscriptionCohorte),
    avec leur identité globale (Participant) - import Excel, manuel et
    auto-inscrits confondus.
    """
    synchroniser_statuts_cohortes()
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    inscriptions = (
        cohorte.inscriptions.select_related("participant", "participant__membre")
        .order_by("participant__nom")
    )
    nombre_seances = cohorte.seances.count()
    presences_par_participant = {}
    for p in PresenceCohorte.objects.filter(seance_cohorte__cohorte=cohorte).values("participant_id"):
        presences_par_participant[p["participant_id"]] = presences_par_participant.get(p["participant_id"], 0) + 1

    return Response([
        {
            "id_inscription": i.id_inscription,
            "id_participant": i.participant.id_participant,
            "nom": i.participant.nom,
            "prenom": i.participant.prenom,
            "telephone": i.participant.telephone,
            "numero_carte_identite": i.participant.numero_carte_identite,
            "numero_badge": i.participant.numero_badge,
            "photo": i.participant.photo.url if i.participant.photo else None,
            "source": i.source,
            "statut": i.statut,
            "a_un_compte": i.participant.membre_id is not None,
            "taux_presence": (
                round(presences_par_participant.get(i.participant.id_participant, 0) / nombre_seances * 100, 1)
                if nombre_seances else 0
            ),
            "kit_distribue": i.kit_distribue,
            "date_distribution_kit": i.date_distribution_kit,
        }
        for i in inscriptions
    ])


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_basculer_kit_distribue(request, id_inscription):
    """
    POST /api/admin/inscription/<id_inscription>/kit/
    Bascule le statut de remise du kit/matériel de travail pour cette
    inscription (aucun corps requis - simple bascule).

    Volontairement PAS bloqué par le verrouillage de la cohorte : marquer
    un kit comme remis a justement lieu APRÈS la fin de la session.
    """
    try:
        inscription = InscriptionCohorte.objects.get(id_inscription=id_inscription)
    except InscriptionCohorte.DoesNotExist:
        return Response({"erreur": "Inscription introuvable"}, status=status.HTTP_404_NOT_FOUND)

    inscription.kit_distribue = not inscription.kit_distribue
    inscription.date_distribution_kit = timezone.now() if inscription.kit_distribue else None
    inscription.save(update_fields=["kit_distribue", "date_distribution_kit"])

    return Response({
        "id_inscription": inscription.id_inscription,
        "kit_distribue": inscription.kit_distribue,
        "date_distribution_kit": inscription.date_distribution_kit,
    })


@api_view(["DELETE"])
@permission_classes([EstAdmin])
def vue_retirer_participant_cohorte(request, id_inscription):
    """
    DELETE /api/admin/inscription/<id_inscription>/
    Retire un participant d'une cohorte (supprime InscriptionCohorte et ses présences associées dans cette cohorte).
    """
    try:
        inscription = InscriptionCohorte.objects.select_related("participant", "cohorte").get(id_inscription=id_inscription)
    except InscriptionCohorte.DoesNotExist:
        return Response({"erreur": "Inscription introuvable"}, status=status.HTTP_404_NOT_FOUND)

    synchroniser_statuts_cohortes()
    inscription.cohorte.refresh_from_db(fields=["statut"])
    erreur = erreur_si_cohorte_verrouillee(inscription.cohorte)
    if erreur:
        return erreur

    participant_nom = f"{inscription.participant.prenom} {inscription.participant.nom}"
    cohorte_code = inscription.cohorte.code_cohorte
    # Supprimer les présences du participant sur cette cohorte
    PresenceCohorte.objects.filter(
        participant=inscription.participant,
        seance_cohorte__cohorte=inscription.cohorte,
    ).delete()
    inscription.delete()

    JournalAudit.objects.create(
        type_action="retrait_participant_cohorte",
        description=f"{participant_nom} retiré de la cohorte {cohorte_code}",
        compte_auteur=request.user,
    )
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_ajouter_seance_cohorte(request, id_cohorte):
    """
    POST /api/admin/cohorte/<id_cohorte>/seances/
    Ajouter une nouvelle séance à une cohorte existante.
    """
    synchroniser_statuts_cohortes()
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    erreur = erreur_si_cohorte_verrouillee(cohorte)
    if erreur:
        return erreur

    donnees = request.data
    date_seance = donnees.get("date_seance")
    if not date_seance:
        return Response({"erreur": "date_seance requise"}, status=status.HTTP_400_BAD_REQUEST)

    dernier_numero = cohorte.seances.count()
    seance = SeanceCohorte.objects.create(
        cohorte=cohorte,
        numero_ordre=dernier_numero + 1,
        date_seance=date_seance,
        heure_fin=donnees.get("heure_fin"),
        lieu=donnees.get("lieu") or cohorte.lieu,
        titre_seance=donnees.get("titre_seance"),
        type_seance=donnees.get("type_seance", "COURS"),
        formateur_seance=donnees.get("formateur_seance") or cohorte.formateur,
    )
    _recalculer_dates_cohorte(cohorte)

    return Response({
        "id_seance_cohorte": seance.id_seance_cohorte,
        "numero_ordre": seance.numero_ordre,
        "date_seance": str(seance.date_seance),
        "titre_seance": seance.titre_seance,
    }, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([EstAdmin])
def vue_gerer_seance_cohorte(request, id_seance_cohorte):
    """
    PATCH /api/admin/seance-cohorte/<id_seance_cohorte>/ - modifier date, lieu, titre, etc.
    DELETE /api/admin/seance-cohorte/<id_seance_cohorte>/ - supprimer si aucune présence ou confirmation
    """
    try:
        seance = SeanceCohorte.objects.select_related("cohorte").get(id_seance_cohorte=id_seance_cohorte)
    except SeanceCohorte.DoesNotExist:
        return Response({"erreur": "Séance introuvable"}, status=status.HTTP_404_NOT_FOUND)

    synchroniser_statuts_cohortes()
    seance.cohorte.refresh_from_db(fields=["statut"])
    erreur = erreur_si_cohorte_verrouillee(seance.cohorte)
    if erreur:
        return erreur

    if request.method == "DELETE":
        nb_presences = seance.presences.count()
        if nb_presences > 0:
            return Response(
                {"erreur": f"Impossible de supprimer : {nb_presences} présence(s) enregistrée(s) pour cette séance."},
                status=status.HTTP_409_CONFLICT,
            )
        cohorte = seance.cohorte
        seance.delete()
        # Réordonner les séances restantes
        for index, s in enumerate(cohorte.seances.order_by("numero_ordre"), start=1):
            if s.numero_ordre != index:
                s.numero_ordre = index
                s.save(update_fields=["numero_ordre"])
        _recalculer_dates_cohorte(cohorte)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # PATCH
    donnees = request.data
    for champ in ["date_seance", "heure_fin", "lieu", "titre_seance", "type_seance", "formateur_seance"]:
        if champ in donnees:
            setattr(seance, champ, donnees[champ])
    seance.save()
    _recalculer_dates_cohorte(seance.cohorte)

    return Response({
        "id_seance_cohorte": seance.id_seance_cohorte,
        "numero_ordre": seance.numero_ordre,
        "date_seance": str(seance.date_seance),
        "heure_fin": str(seance.heure_fin) if seance.heure_fin else None,
        "lieu": seance.lieu,
        "titre_seance": seance.titre_seance,
        "type_seance": seance.type_seance,
        "formateur_seance": seance.formateur_seance,
    })



@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_ajouter_participant_manuel(request, id_cohorte):
    """
    POST /api/admin/cohorte/<id>/participant/
    Corps : {"nom", "prenom", "telephone", "numero_carte_identite" (optionnel)}
    Rapproche par téléphone/CNI si la personne existe déjà (autre
    formation), sinon crée une nouvelle identité + badge.
    """
    synchroniser_statuts_cohortes()
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    erreur = erreur_si_cohorte_verrouillee(cohorte)
    if erreur:
        return erreur

    donnees = request.data
    if not donnees.get("nom") or not donnees.get("prenom"):
        return Response({"erreur": "nom et prenom requis"}, status=status.HTTP_400_BAD_REQUEST)

    telephone = donnees.get("telephone") or None
    cni = donnees.get("numero_carte_identite") or None

    participant = None
    if telephone:
        participant = Participant.objects.filter(telephone=telephone).first()
    if participant is None and cni:
        participant = Participant.objects.filter(numero_carte_identite=cni).first()

    if participant is None:
        nouvel_uuid = uuid_lib.uuid4()
        _, version = generer_signature(str(nouvel_uuid))
        participant = Participant.objects.create(
            nom=donnees["nom"], prenom=donnees["prenom"],
            telephone=telephone, numero_carte_identite=cni,
            uuid=nouvel_uuid, id_version_cle=version,
        )

    try:
        InscriptionCohorte.objects.create(participant=participant, cohorte=cohorte, source="MANUEL")
    except IntegrityError:
        return Response(
            {"erreur": "Cette personne est déjà inscrite à cette cohorte"},
            status=status.HTTP_409_CONFLICT,
        )

    return Response(
        {
            "id_participant": participant.id_participant,
            "numero_badge": participant.numero_badge,
            "badge": construire_contenu_carte(str(participant.uuid)),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_detail_participant(request, id_participant):
    """
    GET /api/admin/participant/<id>/
    Détail complet d'un participant, avec le contenu de son badge
    signé - permet de le consulter/l'imprimer à tout moment, pas
    seulement juste après sa création (vue_ajouter_participant_manuel
    ne renvoie le badge qu'une fois, à l'instant T de la création).
    """
    try:
        participant = Participant.objects.select_related("membre").get(id_participant=id_participant)
    except Participant.DoesNotExist:
        return Response({"erreur": "Participant introuvable"}, status=status.HTTP_404_NOT_FOUND)

    photo_url = None
    if participant.membre and participant.membre.photo:
        photo_url = participant.membre.photo.url
    elif participant.photo:
        photo_url = participant.photo.url

    inscriptions = participant.inscriptions.select_related("cohorte__formation")

    return Response({
        "id_participant": participant.id_participant,
        "nom": participant.nom,
        "prenom": participant.prenom,
        "telephone": participant.telephone,
        "numero_carte_identite": participant.numero_carte_identite,
        "numero_badge": participant.numero_badge,
        "badge": construire_contenu_carte(str(participant.uuid)),
        "photo": photo_url,
        "a_un_compte": participant.membre_id is not None,
        "formations": [
            {
                "code_cohorte": i.cohorte.code_cohorte,
                "formation": i.cohorte.formation.titre,
                "statut": i.statut,
            }
            for i in inscriptions
        ],
    })


@api_view(["PATCH"])
@permission_classes([EstAdmin])
def vue_modifier_participant(request, id_participant):
    """
    PATCH /api/admin/participant/<id>/modifier/
    Corrige l'identité d'un participant (nom, prénom, téléphone, n° de
    carte d'identité) - utile en cas d'erreur de saisie à l'ajout ou à
    l'import Excel. Tous les champs sont optionnels (mise à jour
    partielle). Le téléphone/CNI restent uniques : ce sont eux qui
    servent au rapprochement entre formations, un doublon romprait ce
    mécanisme.
    """
    try:
        participant = Participant.objects.get(id_participant=id_participant)
    except Participant.DoesNotExist:
        return Response({"erreur": "Participant introuvable"}, status=status.HTTP_404_NOT_FOUND)

    synchroniser_statuts_cohortes()
    # Un participant reste modifiable tant qu'il a AU MOINS une inscription
    # à une cohorte encore active (pas encore terminée/annulée) - une fois
    # que TOUTES ses cohortes sont closes, son identité est figée, comme
    # le reste de leurs données (cohérent avec le verrouillage des
    # cohortes). Ça reste possible de corriger une faute de frappe tant
    # que la personne suit encore au moins un cours ailleurs.
    a_une_cohorte_active = participant.inscriptions.filter(
        cohorte__statut__in=["BROUILLON", "PROGRAMMEE", "EN_COURS"]
    ).exists()
    if participant.inscriptions.exists() and not a_une_cohorte_active:
        return Response(
            {"erreur": "Toutes les formations de ce participant sont terminées : son identité ne peut plus être modifiée."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    donnees = request.data

    if "nom" in donnees:
        nom = (donnees.get("nom") or "").strip()
        if not nom:
            return Response({"erreur": "Le nom ne peut pas être vide"}, status=status.HTTP_400_BAD_REQUEST)
        participant.nom = nom

    if "prenom" in donnees:
        prenom = (donnees.get("prenom") or "").strip()
        if not prenom:
            return Response({"erreur": "Le prénom ne peut pas être vide"}, status=status.HTTP_400_BAD_REQUEST)
        participant.prenom = prenom

    if "telephone" in donnees:
        telephone = (donnees.get("telephone") or "").strip() or None
        if telephone and Participant.objects.exclude(pk=participant.pk).filter(telephone=telephone).exists():
            return Response(
                {"erreur": "Ce numéro de téléphone est déjà utilisé par un autre participant"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        participant.telephone = telephone

    if "numero_carte_identite" in donnees:
        cni = (donnees.get("numero_carte_identite") or "").strip() or None
        if cni and Participant.objects.exclude(pk=participant.pk).filter(numero_carte_identite=cni).exists():
            return Response(
                {"erreur": "Ce numéro de carte d'identité est déjà utilisé par un autre participant"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        participant.numero_carte_identite = cni

    participant.save()

    JournalAudit.objects.create(
        type_action="modification_participant",
        description=f"Participant modifié : {participant.prenom} {participant.nom}",
        compte_auteur=request.user,
    )

    return Response({
        "id_participant": participant.id_participant,
        "nom": participant.nom,
        "prenom": participant.prenom,
        "telephone": participant.telephone,
        "numero_carte_identite": participant.numero_carte_identite,
    })


def _normaliser_entete(texte):
    """
    Réduit un en-tête de colonne à sa forme la plus simple possible :
    minuscules, sans accents, sans espaces ni ponctuation. Ex:
    "Numéro de Téléphone" -> "numerodetelephone". Permet de reconnaître
    des en-têtes écrits naturellement (par une personne, pas par un
    développeur), sans exiger un nom de colonne technique exact.
    """
    if texte is None:
        return ""
    texte = str(texte).strip().lower()
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", texte)


# Alias reconnus pour chaque champ, une fois l'en-tête normalisé.
# Couvre les formulations naturelles les plus courantes en français,
# pas seulement le nom technique de la colonne.
_ALIAS_NOM = {"nom", "nomdefamille", "nomeleve", "nomparticipant", "lastname"}
_ALIAS_PRENOM = {"prenom", "prenoms", "firstname"}
_ALIAS_TELEPHONE = {
    "telephone", "numerodetelephone", "numerotelephone", "tel",
    "contact", "phone", "numerocontact",
}
_ALIAS_CNI = {
    "numerocartedidentite", "numerodecartedidentite", "cni",
    "carteidentite", "numeropiece", "piece", "ninea", "numerocni",
}


def _trouver_colonne(entetes_normalisees, alias):
    for index, entete in enumerate(entetes_normalisees):
        if entete in alias:
            return index
    return None


@api_view(["POST"])
@permission_classes([EstAdmin])
@parser_classes([MultiPartParser, FormParser])
def vue_importer_participants_excel(request, id_cohorte):
    """
    POST /api/admin/cohorte/<id>/importer-excel/
    Fichier multipart, champ "fichier". Les en-têtes de colonnes sont
    reconnus de façon SOUPLE (accents, espaces, majuscules ignorés,
    plusieurs formulations acceptées par champ - voir les alias
    ci-dessus) : "Prénom", "prenom" ou "Prénoms" sont tous acceptés.

    RAPPROCHEMENT : si une ligne correspond (téléphone ou CNI) à un
    Participant déjà connu (peu importe la formation d'origine), on
    NE CRÉE PAS de doublon - on réutilise son identité/badge et on
    l'inscrit juste à CETTE nouvelle cohorte. Les lignes déjà
    inscrites à CETTE cohorte précise sont ignorées (pas de double
    inscription si le fichier est envoyé deux fois par erreur).

    Deux passes en bulk_create (participants puis inscriptions),
    jamais une requête par ligne, pour rester rapide même sur
    plusieurs centaines de lignes.
    """
    synchroniser_statuts_cohortes()
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    erreur = erreur_si_cohorte_verrouillee(cohorte)
    if erreur:
        return erreur

    fichier = request.FILES.get("fichier")
    if fichier is None:
        return Response({"erreur": "Fichier manquant (champ 'fichier')"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        classeur = openpyxl.load_workbook(fichier, read_only=True, data_only=True)
        feuille = classeur.active
    except Exception:
        return Response({"erreur": "Fichier Excel invalide"}, status=status.HTTP_400_BAD_REQUEST)

    lignes = list(feuille.iter_rows(values_only=True))
    if len(lignes) < 2:
        return Response({"erreur": "Le fichier ne contient aucune ligne de données"}, status=status.HTTP_400_BAD_REQUEST)

    entetes_normalisees = [_normaliser_entete(c) for c in lignes[0]]
    idx_nom = _trouver_colonne(entetes_normalisees, _ALIAS_NOM)
    idx_prenom = _trouver_colonne(entetes_normalisees, _ALIAS_PRENOM)
    if idx_nom is None or idx_prenom is None:
        return Response(
            {
                "erreur": (
                    "Impossible de reconnaître les colonnes Nom et Prénom. "
                    f"En-têtes trouvés : {', '.join(str(c) for c in lignes[0] if c)}"
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    idx_telephone = _trouver_colonne(entetes_normalisees, _ALIAS_TELEPHONE)
    idx_cni = _trouver_colonne(entetes_normalisees, _ALIAS_CNI)

    # Pré-chargement de TOUS les participants déjà connus par
    # téléphone/CNI (peu importe la formation), pour le rapprochement -
    # deux requêtes au total, pas une par ligne du fichier.
    participants_par_telephone = {
        p.telephone: p for p in Participant.objects.exclude(telephone__isnull=True)
    }
    participants_par_cni = {
        p.numero_carte_identite: p
        for p in Participant.objects.exclude(numero_carte_identite__isnull=True)
    }
    deja_inscrits = set(
        cohorte.inscriptions.values_list("participant_id", flat=True)
    )

    a_creer_participants = []
    a_creer_inscriptions = []
    reconnus = 0
    nouveaux = 0
    ignores = 0

    for ligne in lignes[1:]:
        if not ligne or not ligne[idx_nom] or not ligne[idx_prenom]:
            continue

        telephone = str(ligne[idx_telephone]).strip() if idx_telephone is not None and ligne[idx_telephone] else None
        cni = str(ligne[idx_cni]).strip() if idx_cni is not None and ligne[idx_cni] else None

        participant_existant = None
        if telephone and telephone in participants_par_telephone:
            participant_existant = participants_par_telephone[telephone]
        elif cni and cni in participants_par_cni:
            participant_existant = participants_par_cni[cni]

        if participant_existant:
            if participant_existant.id_participant in deja_inscrits:
                ignores += 1
                continue
            a_creer_inscriptions.append(InscriptionCohorte(
                participant=participant_existant, cohorte=cohorte, source="EXCEL",
            ))
            deja_inscrits.add(participant_existant.id_participant)
            reconnus += 1
        else:
            nouvel_uuid = uuid_lib.uuid4()
            _, version = generer_signature(str(nouvel_uuid))
            nouveau_participant = Participant(
                nom=str(ligne[idx_nom]).strip(),
                prenom=str(ligne[idx_prenom]).strip(),
                telephone=telephone,
                numero_carte_identite=cni,
                uuid=nouvel_uuid,
                id_version_cle=version,
            )
            a_creer_participants.append(nouveau_participant)
            # On mémorise déjà la clé pour éviter un doublon SI le
            # même téléphone apparaît deux fois dans le même fichier.
            if telephone:
                participants_par_telephone[telephone] = nouveau_participant
            if cni:
                participants_par_cni[cni] = nouveau_participant
            nouveaux += 1

    # bulk_create renvoie les objets avec leur PK réellement assignée
    # (SQLite/PostgreSQL récents) - nécessaire pour créer les
    # inscriptions juste après dans la seconde passe.
    participants_crees = Participant.objects.bulk_create(a_creer_participants)
    for p in participants_crees:
        a_creer_inscriptions.append(
            InscriptionCohorte(participant=p, cohorte=cohorte, source="EXCEL")
        )
    InscriptionCohorte.objects.bulk_create(a_creer_inscriptions)

    JournalAudit.objects.create(
        type_action="import_excel_cohorte",
        description=(
            f"{nouveaux} nouvelle(s) identité(s), {reconnus} personne(s) reconnue(s) "
            f"(déjà badgées ailleurs), {ignores} doublon(s) ignoré(s) - {cohorte.code_cohorte}"
        ),
        compte_auteur=request.user,
    )

    return Response({"nouveaux": nouveaux, "reconnus": reconnus, "ignores": ignores})


@api_view(["GET"])
@permission_classes([PeutControler])
def vue_seances_cohorte(request, id_cohorte):
    """GET /api/cohorte/<id>/seances/ - pour le contrôleur, avant de scanner une session de cours."""
    try:
        cohorte = Cohorte.objects.get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    seances = cohorte.seances.order_by("numero_ordre")
    return Response([
        {
            "id_seance_cohorte": s.id_seance_cohorte,
            "numero_ordre": s.numero_ordre,
            "date_seance": s.date_seance,
            "heure_fin": s.heure_fin,
            "lieu": s.lieu or cohorte.lieu,
            "titre_seance": s.titre_seance,
            "type_seance": s.type_seance,
            "formateur_seance": s.formateur_seance or cohorte.formateur,
        }
        for s in seances
    ])


@api_view(["GET"])
@permission_classes([PeutControler])
def vue_verifier_participant(request, uuid_participant, version, signature):
    """
    GET /api/verifier-participant/<uuid>/<version>/<signature>/
    Vérifie un badge de participant (identité globale). La photo
    affichée pour confirmation visuelle vient de son compte lié s'il
    en a un (mise à jour depuis "Profil"), sinon de la photo ajoutée
    directement par un organisateur.
    """
    if not verifier_signature(str(uuid_participant), signature, version):
        return Response(
            {"valide": False, "erreur": "Signature invalide"}, status=status.HTTP_401_UNAUTHORIZED
        )

    try:
        participant = Participant.objects.select_related("membre").get(uuid=uuid_participant)
    except Participant.DoesNotExist:
        return Response({"valide": False, "erreur": "Badge introuvable"}, status=status.HTTP_404_NOT_FOUND)

    photo_url = None
    if participant.membre and participant.membre.photo:
        photo_url = participant.membre.photo.url
    elif participant.photo:
        photo_url = participant.photo.url

    return Response({
        "valide": True,
        "id_participant": participant.id_participant,
        "nom": participant.nom,
        "prenom": participant.prenom,
        "photo": photo_url,
    })


@api_view(["POST"])
@permission_classes([PeutControler])
def vue_confirmer_presence_cohorte(request):
    """
    POST /api/confirmer-presence-cohorte/
    Corps : {"id_participant", "id_seance_cohorte", "methode_scan"}
    """
    id_participant = request.data.get("id_participant")
    id_seance_cohorte = request.data.get("id_seance_cohorte")
    methode_scan = request.data.get("methode_scan")

    if not all([id_participant, id_seance_cohorte, methode_scan]):
        return Response(
            {"erreur": "id_participant, id_seance_cohorte et methode_scan sont requis"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if methode_scan not in dict(Participe.METHODE_CHOICES):
        return Response({"erreur": "methode_scan invalide"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        participant = Participant.objects.get(id_participant=id_participant)
    except Participant.DoesNotExist:
        return Response({"erreur": "Participant introuvable"}, status=status.HTTP_404_NOT_FOUND)

    try:
        seance_cohorte = SeanceCohorte.objects.select_related("cohorte").get(
            id_seance_cohorte=id_seance_cohorte
        )
    except SeanceCohorte.DoesNotExist:
        return Response({"erreur": "Séance introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if seance_cohorte.cohorte.statut == "TERMINEE":
        return Response(
            {"erreur": "Cette cohorte est terminée, plus aucun scan n'est accepté"},
            status=status.HTTP_403_FORBIDDEN,
        )

    controleur, _ = Controleur.objects.get_or_create(
        compte=request.user,
        defaults={"date_nomination": timezone.now().date()},
    )

    try:
        presence = PresenceCohorte.objects.create(
            participant=participant,
            seance_cohorte=seance_cohorte,
            heure_arrivee=timezone.now(),
            methode_scan=methode_scan,
            controleur_scan=controleur,
        )
    except IntegrityError:
        return Response(
            {"erreur": "Ce participant est déjà enregistré présent à cette séance"},
            status=status.HTTP_409_CONFLICT,
        )

    return Response(
        {
            "confirme": True,
            "participant": f"{participant.prenom} {participant.nom}",
            "cohorte": seance_cohorte.cohorte.code_cohorte,
            "seance": seance_cohorte.numero_ordre,
            "heure_arrivee": presence.heure_arrivee,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_detail_cohorte(request, id_cohorte):
    """
    GET /api/admin/cohorte/<id>/
    Statistiques : participants (tous, compte ou non), présence par
    séance, taux d'assiduité global.
    """
    synchroniser_statuts_cohortes()
    try:
        cohorte = Cohorte.objects.select_related("formation").get(id_cohorte=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    inscriptions = cohorte.inscriptions.select_related("participant")
    nombre_participants = inscriptions.count()
    nombre_seances = cohorte.seances.count()

    presences = (
        PresenceCohorte.objects.filter(seance_cohorte__cohorte=cohorte)
        .select_related("participant", "seance_cohorte")
    )

    presence_par_seance = {}
    for p in presences:
        cle = f"Séance {p.seance_cohorte.numero_ordre}"
        presence_par_seance[cle] = presence_par_seance.get(cle, 0) + 1

    presences_possibles = nombre_participants * nombre_seances
    taux_assiduite = round((presences.count() / presences_possibles) * 100, 1) if presences_possibles else 0

    return Response({
        "id_cohorte": cohorte.id_cohorte,
        "code_cohorte": cohorte.code_cohorte,
        "formation": cohorte.formation.titre,
        "statut": cohorte.statut,
        "verrouillee": cohorte.statut in ("TERMINEE", "ANNULEE"),
        "lieu": cohorte.lieu,
        "formateur": cohorte.formateur,
        "nombre_seances": nombre_seances,
        "nombre_participants": nombre_participants,
        "taux_assiduite": taux_assiduite,
        "capacite_max": cohorte.capacite_max,
        "capacite_min": cohorte.capacite_min,
        "materiel_necessaire": cohorte.materiel_necessaire,
        "est_payante": cohorte.est_payante,
        "prix": str(cohorte.prix) if cohorte.prix is not None else None,
        "prix_adherent": str(cohorte.prix_adherent) if cohorte.prix_adherent is not None else None,
        "date_limite_inscription": cohorte.date_limite_inscription,
        "conditions_annulation": cohorte.conditions_annulation,
        "financeur": cohorte.financeur,
        "numero_convention": cohorte.numero_convention,
        "seuil_certification": cohorte.seuil_certification,
        "public_cible_type": cohorte.public_cible_type,
        "participants": [
            {
                "id_participant": i.participant.id_participant,
                "nom": i.participant.nom,
                "prenom": i.participant.prenom,
                "source": i.source,
                "a_un_compte": i.participant.membre_id is not None,
                "statut": i.statut,
            }
            for i in inscriptions
        ],
        "presence_par_seance": presence_par_seance,
    })


@api_view(["POST"])
@permission_classes([EstAdmin])
@parser_classes([MultiPartParser, FormParser])
def vue_televerser_photo_participant(request, id_participant):
    """
    POST /api/admin/participant/<id>/photo/
    Champ "photo" multipart - pour un participant SANS compte
    (uniquement un organisateur peut lui ajouter une photo).
    """
    try:
        participant = Participant.objects.get(id_participant=id_participant)
    except Participant.DoesNotExist:
        return Response({"erreur": "Participant introuvable"}, status=status.HTTP_404_NOT_FOUND)

    photo = request.FILES.get("photo")
    if photo is None:
        return Response({"erreur": "Fichier manquant (champ 'photo')"}, status=status.HTTP_400_BAD_REQUEST)

    participant.photo = photo
    participant.save()
    return Response({"photo": participant.photo.url})


@api_view(["POST"])
@permission_classes([EstAdmin])
def vue_creer_membre_admin(request):
    """
    POST /api/admin/membre/creer/
    Création d'un membre par l'administrateur. Deux modes :

    Mode 1 - Manuel (mode="MANUEL") :
        Corps : {"nom", "prenom", "email", "mot_de_passe", "fonction_association", "id_section" (optionnel), ...}
        Le compte est créé avec doit_changer_mot_de_passe=True.
        Réponse inclut login et mot de passe pour que l'admin les transmette.

    Mode 2 - Email automatique (mode="EMAIL") :
        Corps : {"nom", "prenom", "email", "fonction_association", "id_section" (optionnel), ...}
        Un mot de passe est généré automatiquement.
        Un email est envoyé au membre avec ses identifiants.

    "fonction_association" est le champ d'identité principal du membre
    (voir Membre.FONCTION_CHOICES) ; "id_section" est désormais
    optionnel (la localité géographique n'est pas ce qui définit un
    membre AP2A).
    """
    donnees = request.data
    mode = donnees.get("mode", "MANUEL")

    champs_requis = ["nom", "prenom"]
    if not all(donnees.get(c) for c in champs_requis):
        return Response(
            {"erreur": f"Champs requis : {', '.join(champs_requis)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    fonction_association = donnees.get("fonction_association", "MEMBRE_ACTIF")
    if fonction_association not in dict(Membre.FONCTION_CHOICES):
        return Response({"erreur": "Fonction AP2A invalide"}, status=status.HTTP_400_BAD_REQUEST)

    email = donnees.get("email", "").strip()
    if not email:
        # Pas d'email : on génère un identifiant unique
        # basé sur le nom+prénom+compteur pour les membres sans email
        base = f"{donnees['prenom'].strip().lower()}.{donnees['nom'].strip().lower()}"
        base = re.sub(r'[^a-z0-9.]', '', unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode())
        candidat = f"{base}@membre.ap2a.local"
        compteur = 1
        while Compte.objects.filter(email=candidat).exists():
            candidat = f"{base}{compteur}@membre.ap2a.local"
            compteur += 1
        email = candidat

    if Compte.objects.filter(email=email).exists():
        return Response(
            {"erreur": "Cet email est déjà utilisé"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    section = None
    if donnees.get("id_section"):
        try:
            section = Section.objects.get(id_section=donnees["id_section"])
        except Section.DoesNotExist:
            return Response({"erreur": "Section introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if mode == "MANUEL":
        mot_de_passe = donnees.get("mot_de_passe", "").strip()
        if not mot_de_passe:
            return Response(
                {"erreur": "Le mot de passe est requis en mode manuel"},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        # Mode EMAIL : générer un mot de passe aléatoire
        mot_de_passe = secrets.token_urlsafe(10)

    compte = Compte(
        email=email,
        nom=donnees["nom"].strip(),
        prenom=donnees["prenom"].strip(),
        telephone=donnees.get("telephone") or None,
        doit_changer_mot_de_passe=True,
    )
    compte.definir_mot_de_passe(mot_de_passe)
    compte.save()

    numero_adherent = f"ADH-{Membre.objects.count() + 1:04d}"
    membre = Membre.objects.create(
        numero_adherent=numero_adherent,
        date_adhesion=timezone.now().date(),
        compte=compte,
        section=section,
        fonction_association=fonction_association,
    )

    nouvel_uuid = str(uuid_lib.uuid4())
    Carte.objects.create(
        uuid=nouvel_uuid,
        type_carte="QR",
        id_version_cle=settings.HMAC_VERSION_ACTIVE,
        membre=membre,
    )

    # En mode EMAIL, tenter d'envoyer un email
    email_envoye = False
    if mode == "EMAIL" and email and not email.endswith("@membre.ap2a.local"):
        try:
            from django.core.mail import send_mail
            send_mail(
                subject="Bienvenue sur AP2A - Vos identifiants",
                message=(
                    f"Bonjour {compte.prenom},\n\n"
                    f"Votre compte AP2A a été créé.\n\n"
                    f"Identifiant : {email}\n"
                    f"Numéro adhérent : {numero_adherent}\n"
                    f"Mot de passe temporaire : {mot_de_passe}\n\n"
                    f"Vous devrez changer ce mot de passe lors de votre première connexion.\n\n"
                    f"Cordialement,\nL'association AP2A"
                ),
                from_email=None,  # Utilise DEFAULT_FROM_EMAIL
                recipient_list=[email],
                fail_silently=False,
            )
            email_envoye = True
        except Exception:
            email_envoye = False

    JournalAudit.objects.create(
        type_action="creation_membre_admin",
        description=f"Membre créé par admin : {compte.prenom} {compte.nom} ({numero_adherent})",
        compte_auteur=request.user,
    )

    return Response(
        {
            "id_membre": membre.id_membre,
            "numero_adherent": numero_adherent,
            "email": email,
            "mot_de_passe_temporaire": mot_de_passe if mode == "MANUEL" else None,
            "email_envoye": email_envoye,
            "mode": mode,
        },
        status=status.HTTP_201_CREATED,
    )
"""
adhesion/views_nouveaux_modules.py

Vues API pour les modules ajoutés à la plateforme AP2A :
- Gestion des permissions admin (super admin uniquement)
- Actions sociales (CRUD + bénéficiaires + import Excel)
- Suivi post-formation
- Notifications in-app
- Confirmation de présence aux événements
- Invités externes aux événements
- Dashboard / statistiques temps réel
"""

import uuid as uuid_lib
import unicodedata
import re
import openpyxl

from django.utils import timezone
from django.db import IntegrityError
from django.db.models import Count, Q, F
from django.conf import settings

from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework import status

from .models import (
    Compte, Membre, Evenement, Seance, Participe,
    Formation, Cohorte, Participant, InscriptionCohorte, SeanceCohorte, PresenceCohorte,
    PermissionAdmin, ConfirmationEvenement, InviteExterne,
    ActionSociale, Beneficiaire, ParticipationAction,
    DetailCommerce, DetailAtelier, DetailMedical,
    SuiviPostFormation, Notification, ConfigAssociation,
    JournalAudit, Kit, DistributionKit, CompteRendu, Actualite,
)
from .permissions import (
    EstAuthentifie, EstMembre, EstAdmin, EstSuperAdmin,
    PeutControler, APermission, compte_a_permission,
)
from .utils import (
    est_email_reel, synchroniser_statuts_cohortes, erreur_si_cohorte_verrouillee,
    envoyer_email_arriere_plan, generer_mot_de_passe_temporaire,
)


# =========================================================================
# GESTION DES PERMISSIONS ADMIN (super admin uniquement)
# =========================================================================

@api_view(["POST"])
@permission_classes([EstSuperAdmin])
def vue_nommer_admin_avec_permissions(request):
    """
    POST /api/admin/nommer-admin-v2/
    Le super admin désigne un compte comme admin et lui attribue
    des permissions spécifiques.

    Corps attendu :
    {
        "id_compte": 42,
        "permissions": ["GERER_MEMBRES", "GERER_EVENEMENTS"]
    }
    """
    id_compte = request.data.get("id_compte")
    permissions = request.data.get("permissions", [])

    if not id_compte:
        return Response(
            {"erreur": "id_compte est requis"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        compte = Compte.objects.get(pk=id_compte)
    except Compte.DoesNotExist:
        return Response(
            {"erreur": "Compte introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if compte.est_super_admin:
        return Response(
            {"erreur": "Impossible de modifier les permissions du super admin"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Codes de permissions valides
    codes_valides = {code for code, _ in PermissionAdmin.PERMISSION_CHOICES}
    codes_demandes = set(permissions)
    codes_invalides = codes_demandes - codes_valides

    if codes_invalides:
        return Response(
            {"erreur": f"Permissions inconnues : {', '.join(codes_invalides)}",
             "permissions_valides": list(codes_valides)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Activer le statut admin
    compte.est_admin = True
    compte.compte_nommant = request.user
    compte.save(update_fields=["est_admin", "compte_nommant_id"])

    # Supprimer les anciennes permissions et attribuer les nouvelles
    PermissionAdmin.objects.filter(compte=compte).delete()
    for code in codes_demandes:
        PermissionAdmin.objects.create(
            compte=compte,
            code_permission=code,
            attribue_par=request.user,
        )

    # Audit
    JournalAudit.objects.create(
        type_action="NOMINATION_ADMIN",
        description=f"{request.user.prenom} {request.user.nom} a nommé "
                    f"{compte.prenom} {compte.nom} admin avec permissions : "
                    f"{', '.join(codes_demandes)}",
        compte_auteur=request.user,
    )

    # Notification au nouvel admin
    Notification.objects.create(
        compte_destinataire=compte,
        titre="Vous êtes maintenant administrateur",
        corps=f"Le super admin vous a attribué les droits suivants : "
              f"{', '.join(codes_demandes)}.",
        type_notification="SUCCES",
    )

    return Response({
        "message": f"{compte.prenom} {compte.nom} nommé admin",
        "permissions": list(codes_demandes),
    })


@api_view(["PUT"])
@permission_classes([EstSuperAdmin])
def vue_modifier_permissions_admin(request, id_compte):
    """
    PUT /api/admin/permissions/<id_compte>/
    Modifier les permissions d'un admin existant.

    Corps attendu :
    {"permissions": ["GERER_MEMBRES", "VOIR_RAPPORTS"]}
    """
    try:
        compte = Compte.objects.get(pk=id_compte)
    except Compte.DoesNotExist:
        return Response(
            {"erreur": "Compte introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not compte.est_admin:
        return Response(
            {"erreur": "Ce compte n'est pas admin"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if compte.est_super_admin:
        return Response(
            {"erreur": "Impossible de modifier les permissions du super admin"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    permissions = request.data.get("permissions", [])
    codes_valides = {code for code, _ in PermissionAdmin.PERMISSION_CHOICES}
    codes_demandes = set(permissions)

    if codes_demandes - codes_valides:
        return Response(
            {"erreur": f"Permissions inconnues : {', '.join(codes_demandes - codes_valides)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    PermissionAdmin.objects.filter(compte=compte).delete()
    for code in codes_demandes:
        PermissionAdmin.objects.create(
            compte=compte,
            code_permission=code,
            attribue_par=request.user,
        )

    JournalAudit.objects.create(
        type_action="MODIFICATION_PERMISSIONS",
        description=f"Permissions de {compte.prenom} {compte.nom} modifiées : "
                    f"{', '.join(codes_demandes)}",
        compte_auteur=request.user,
    )

    return Response({
        "message": "Permissions mises à jour",
        "permissions": list(codes_demandes),
    })


@api_view(["GET"])
@permission_classes([EstSuperAdmin])
def vue_detail_admin_permissions(request, id_compte):
    """
    GET /api/admin/permissions/<id_compte>/
    Voir les permissions d'un admin.
    """
    try:
        compte = Compte.objects.get(pk=id_compte)
    except Compte.DoesNotExist:
        return Response(
            {"erreur": "Compte introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if compte.est_super_admin:
        permissions = [code for code, _ in PermissionAdmin.PERMISSION_CHOICES]
    else:
        permissions = list(
            PermissionAdmin.objects.filter(compte=compte)
            .values_list("code_permission", flat=True)
        )

    return Response({
        "id_compte": compte.id_compte,
        "nom": compte.nom,
        "prenom": compte.prenom,
        "est_super_admin": compte.est_super_admin,
        "permissions": permissions,
        "permissions_disponibles": [
            {"code": code, "libelle": libelle}
            for code, libelle in PermissionAdmin.PERMISSION_CHOICES
        ],
    })


# =========================================================================
# ACTIONS SOCIALES
# =========================================================================

@api_view(["POST"])
@permission_classes([APermission("GERER_ACTIONS_SOCIALES")])
def vue_creer_action_sociale(request):
    """
    POST /api/admin/action-sociale/
    Créer une nouvelle action sociale.
    """
    champs_requis = ["titre", "type_action", "date_debut"]
    for champ in champs_requis:
        if not request.data.get(champ):
            return Response(
                {"erreur": f"Le champ '{champ}' est requis"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    types_valides = {code for code, _ in ActionSociale.TYPE_CHOICES}
    type_action = request.data["type_action"]
    if type_action not in types_valides:
        return Response(
            {"erreur": f"Type invalide. Types acceptés : {', '.join(types_valides)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    action = ActionSociale.objects.create(
        titre=request.data["titre"],
        type_action=type_action,
        description=request.data.get("description", ""),
        lieu=request.data.get("lieu", ""),
        date_debut=request.data["date_debut"],
        date_fin=request.data.get("date_fin"),
        statut=request.data.get("statut", "PLANIFIE"),
        contenu_don=request.data.get("contenu_don", ""),
        compte_organisateur=request.user,
    )

    JournalAudit.objects.create(
        type_action="CREATION_ACTION_SOCIALE",
        description=f"Action sociale créée : {action.titre} ({action.get_type_action_display()})",
        compte_auteur=request.user,
    )

    return Response({
        "message": "Action sociale créée",
        "id_action": action.id_action,
        "titre": action.titre,
    }, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([APermission("GERER_ACTIONS_SOCIALES")])
def vue_liste_actions_sociales(request):
    """
    GET /api/actions-sociales/
    Liste de toutes les actions sociales avec nombre de bénéficiaires.
    Filtrable par ?type=COMMERCE&statut=EN_COURS
    """
    actions = ActionSociale.objects.annotate(
        nb_beneficiaires=Count("participations"),
    )

    type_filtre = request.query_params.get("type")
    if type_filtre:
        actions = actions.filter(type_action=type_filtre)

    statut_filtre = request.query_params.get("statut")
    if statut_filtre:
        actions = actions.filter(statut=statut_filtre)

    return Response([
        {
            "id_action": a.id_action,
            "titre": a.titre,
            "type_action": a.type_action,
            "type_action_libelle": a.get_type_action_display(),
            "description": a.description,
            "lieu": a.lieu,
            "date_debut": str(a.date_debut),
            "date_fin": str(a.date_fin) if a.date_fin else None,
            "statut": a.statut,
            "nb_beneficiaires": a.nb_beneficiaires,
            "organisateur": f"{a.compte_organisateur.prenom} {a.compte_organisateur.nom}",
        }
        for a in actions
    ])


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([APermission("GERER_ACTIONS_SOCIALES")])
def vue_detail_action_sociale(request, id_action):
    """
    GET /api/admin/action-sociale/<id>/
    PUT/PATCH /api/admin/action-sociale/<id>/
    DELETE /api/admin/action-sociale/<id>/
    """
    try:
        action = ActionSociale.objects.get(pk=id_action)
    except ActionSociale.DoesNotExist:
        return Response(
            {"erreur": "Action sociale introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "DELETE":
        titre = action.titre
        action.delete()
        JournalAudit.objects.create(
            type_action="SUPPRESSION_ACTION_SOCIALE",
            description=f"Action sociale supprimée : {titre}",
            compte_auteur=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    if request.method == "GET":
        participations = (
            ParticipationAction.objects.filter(action=action)
            .select_related("beneficiaire")
            .prefetch_related("detail_medical", "detail_commerce", "detail_atelier")
        )
        return Response({
            "id_action": action.id_action,
            "titre": action.titre,
            "type_action": action.type_action,
            "type_action_libelle": action.get_type_action_display(),
            "description": action.description,
            "lieu": action.lieu,
            "date_debut": str(action.date_debut),
            "date_fin": str(action.date_fin) if action.date_fin else None,
            "statut": action.statut,
            "contenu_don": action.contenu_don,
            "organisateur": f"{action.compte_organisateur.prenom} {action.compte_organisateur.nom}",
            "beneficiaires": [
                {
                    "id_beneficiaire": p.beneficiaire.id_beneficiaire,
                    "id_participation": p.id_participation,
                    "nom": p.beneficiaire.nom,
                    "prenom": p.beneficiaire.prenom,
                    "telephone": p.beneficiaire.telephone,
                    "sexe": p.beneficiaire.sexe,
                    "date_naissance": p.beneficiaire.date_naissance,
                    "numero_identification": p.beneficiaire.numero_identification,
                    "adresse": p.beneficiaire.adresse,
                    "statut": p.statut,
                    "notes": p.notes,
                    "type_aide_recue": p.type_aide_recue,
                    "don_recu": p.don_recu,
                    "date_reception_don": str(p.date_reception_don) if p.date_reception_don else None,
                    "detail_medical": {
                        "a_ete_visite": getattr(getattr(p, "detail_medical", None), "a_ete_visite", False),
                        "date_visite": str(p.detail_medical.date_visite) if hasattr(p, "detail_medical") and p.detail_medical.date_visite else None,
                        "necessite_traitement": getattr(getattr(p, "detail_medical", None), "necessite_traitement", False),
                        "description_besoin_traitement": getattr(getattr(p, "detail_medical", None), "description_besoin_traitement", "") or "",
                        "traitement_effectue": getattr(getattr(p, "detail_medical", None), "traitement_effectue", False),
                        "date_traitement": str(p.detail_medical.date_traitement) if hasattr(p, "detail_medical") and p.detail_medical.date_traitement else None,
                        "notes_suivi": getattr(getattr(p, "detail_medical", None), "notes_suivi", "") or "",
                    } if hasattr(p, "detail_medical") else None,
                }
                for p in participations
            ],
        })

    # PUT / PATCH - modification
    for champ in ["titre", "description", "lieu", "date_debut", "date_fin", "statut", "type_action", "contenu_don"]:
        if champ in request.data:
            setattr(action, champ, request.data[champ])
    action.save()

    JournalAudit.objects.create(
        type_action="MODIFICATION_ACTION_SOCIALE",
        description=f"Action sociale modifiée : {action.titre} (statut: {action.statut})",
        compte_auteur=request.user,
    )

    return Response({
        "message": "Action sociale mise à jour",
        "id_action": action.id_action,
        "titre": action.titre,
        "statut": action.statut,
    })


@api_view(["POST"])
@permission_classes([APermission("GERER_ACTIONS_SOCIALES")])
def vue_ajouter_beneficiaire(request, id_action):
    """
    POST /api/admin/action-sociale/<id>/beneficiaire/
    Enregistrer un bénéficiaire pour une action sociale.
    Si le téléphone existe déjà, on réutilise le bénéficiaire existant.
    """
    try:
        action = ActionSociale.objects.get(pk=id_action)
    except ActionSociale.DoesNotExist:
        return Response(
            {"erreur": "Action sociale introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    nom = request.data.get("nom", "").strip()
    prenom = request.data.get("prenom", "").strip()
    telephone = request.data.get("telephone", "").strip()

    if not nom or not prenom:
        return Response(
            {"erreur": "nom et prenom sont requis"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Dédoublonnage par téléphone
    beneficiaire = None
    if telephone:
        beneficiaire = Beneficiaire.objects.filter(telephone=telephone).first()

    if not beneficiaire:
        beneficiaire = Beneficiaire.objects.create(
            nom=nom,
            prenom=prenom,
            telephone=telephone or None,
            sexe=request.data.get("sexe"),
            date_naissance=request.data.get("date_naissance"),
            numero_identification=request.data.get("numero_identification"),
            adresse=request.data.get("adresse"),
        )

    # Inscription à l'action
    participation, created = ParticipationAction.objects.get_or_create(
        beneficiaire=beneficiaire,
        action=action,
        defaults={
            "statut": "EN_ATTENTE",
            "notes": request.data.get("notes", ""),
            "type_aide_recue": request.data.get("type_aide_recue", ""),
        },
    )

    if not created:
        return Response(
            {"erreur": "Ce bénéficiaire est déjà inscrit à cette action"},
            status=status.HTTP_409_CONFLICT,
        )

    # Créer le détail spécifique selon le type d'action
    if action.type_action == "COMMERCE" and request.data.get("type_commerce"):
        DetailCommerce.objects.create(
            participation=participation,
            type_commerce=request.data["type_commerce"],
            localisation=request.data.get("localisation_commerce"),
            capital_depart_fourni=request.data.get("capital_depart_fourni", False),
            montant_accompagnement=request.data.get("montant_accompagnement"),
        )
    elif action.type_action == "ATELIER" and request.data.get("domaine_atelier"):
        DetailAtelier.objects.create(
            participation=participation,
            domaine_atelier=request.data["domaine_atelier"],
            equipement_fourni=request.data.get("equipement_fourni"),
            local_mis_a_disposition=request.data.get("local_mis_a_disposition", False),
        )
    elif action.type_action == "MEDICAL" and request.data.get("type_soin"):
        DetailMedical.objects.create(
            participation=participation,
            date_consultation=request.data.get("date_consultation", timezone.now().date()),
            type_soin=request.data["type_soin"],
            medecin_referent=request.data.get("medecin_referent"),
            traitement_fourni=request.data.get("traitement_fourni", False),
        )

    return Response({
        "message": "Bénéficiaire enregistré",
        "id_beneficiaire": beneficiaire.id_beneficiaire,
        "id_participation": participation.id_participation,
        "nouveau": created,
    }, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([APermission("GERER_ACTIONS_SOCIALES")])
def vue_modifier_beneficiaire(request, id_beneficiaire):
    """
    PATCH /api/admin/beneficiaire/<id>/
    Modifie les informations d'identité du bénéficiaire (champs
    optionnels, seuls ceux présents dans le corps sont mis à jour).
    """
    try:
        beneficiaire = Beneficiaire.objects.get(pk=id_beneficiaire)
    except Beneficiaire.DoesNotExist:
        return Response({"erreur": "Bénéficiaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    champs_modifiables = ["nom", "prenom", "telephone", "sexe", "date_naissance", "numero_identification", "adresse"]
    for champ in champs_modifiables:
        if champ in request.data:
            setattr(beneficiaire, champ, request.data[champ])
    beneficiaire.save()

    return Response({"message": "Bénéficiaire mis à jour", "id_beneficiaire": beneficiaire.id_beneficiaire})


@api_view(["PATCH", "DELETE"])
@permission_classes([APermission("GERER_ACTIONS_SOCIALES")])
def vue_modifier_ou_retirer_participation(request, id_action, id_participation):
    """
    PATCH /api/admin/action-sociale/<id_action>/participation/<id_participation>/
    Corps : {"statut"?, "notes"?, "type_aide_recue"?} - c'est ici que
    l'on marque concrètement ce qu'un bénéficiaire a reçu.

    DELETE : retire le bénéficiaire de CETTE action (supprime la
    ParticipationAction), sans toucher à son identité globale
    Beneficiaire (réutilisable sur une autre action).
    """
    try:
        participation = ParticipationAction.objects.get(pk=id_participation, action_id=id_action)
    except ParticipationAction.DoesNotExist:
        return Response({"erreur": "Participation introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        if participation.don_recu:
            return Response(
                {"erreur": "Impossible de retirer un bénéficiaire ayant déjà reçu son don (garantie de traçabilité et transparence)."},
                status=status.HTTP_409_CONFLICT,
            )
        participation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    statut = request.data.get("statut")
    if statut is not None and statut not in dict(ParticipationAction.STATUT_CHOICES):
        return Response({"erreur": "Statut invalide"}, status=status.HTTP_400_BAD_REQUEST)

    champs_modifiables = ["statut", "notes", "type_aide_recue"]
    for champ in champs_modifiables:
        if champ in request.data:
            setattr(participation, champ, request.data[champ])
    participation.save()

    return Response({
        "id_participation": participation.id_participation,
        "statut": participation.statut,
        "notes": participation.notes,
        "type_aide_recue": participation.type_aide_recue,
    })


@api_view(["POST"])
@permission_classes([APermission("IMPORTER_DONNEES")])
@parser_classes([MultiPartParser, FormParser])
def vue_importer_beneficiaires_excel(request, id_action):
    """
    POST /api/admin/action-sociale/<id>/importer-excel/
    Importe une liste de bénéficiaires depuis un fichier Excel.

    Colonnes attendues : nom, prenom, telephone, sexe (optionnel),
    adresse (optionnel), notes (optionnel)
    """
    try:
        action = ActionSociale.objects.get(pk=id_action)
    except ActionSociale.DoesNotExist:
        return Response(
            {"erreur": "Action sociale introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    fichier = request.FILES.get("fichier")
    if not fichier:
        return Response(
            {"erreur": "Aucun fichier fourni"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        classeur = openpyxl.load_workbook(fichier, read_only=True)
    except Exception:
        return Response(
            {"erreur": "Fichier Excel illisible"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    feuille = classeur.active
    lignes = list(feuille.iter_rows(min_row=2, values_only=True))

    crees = 0
    doublons = 0
    erreurs = []

    for i, ligne in enumerate(lignes, start=2):
        if len(ligne) < 2:
            erreurs.append(f"Ligne {i} : colonnes insuffisantes")
            continue

        nom = str(ligne[0] or "").strip()
        prenom = str(ligne[1] or "").strip()
        telephone = str(ligne[2] or "").strip() if len(ligne) > 2 else ""

        if not nom or not prenom:
            erreurs.append(f"Ligne {i} : nom ou prénom manquant")
            continue

        # Dédoublonnage
        beneficiaire = None
        if telephone:
            beneficiaire = Beneficiaire.objects.filter(telephone=telephone).first()

        if not beneficiaire:
            beneficiaire = Beneficiaire.objects.create(
                nom=nom,
                prenom=prenom,
                telephone=telephone or None,
                sexe=str(ligne[3] or "").strip().upper()[:1] if len(ligne) > 3 else None,
                adresse=str(ligne[4] or "").strip() if len(ligne) > 4 else None,
            )

        _, created = ParticipationAction.objects.get_or_create(
            beneficiaire=beneficiaire,
            action=action,
            defaults={
                "statut": "EN_ATTENTE",
                "notes": str(ligne[5] or "").strip() if len(ligne) > 5 else "",
            },
        )

        if created:
            crees += 1
        else:
            doublons += 1

    classeur.close()

    JournalAudit.objects.create(
        type_action="IMPORT_BENEFICIAIRES",
        description=f"Import Excel pour '{action.titre}' : {crees} créés, {doublons} doublons, {len(erreurs)} erreurs",
        compte_auteur=request.user,
    )

    return Response({
        "message": f"Import terminé : {crees} bénéficiaires ajoutés",
        "crees": crees,
        "doublons": doublons,
        "erreurs": erreurs,
    })


# =========================================================================
# CONFIRMATION D'ÉVÉNEMENT (intention de présence)
# =========================================================================

@api_view(["GET"])
@permission_classes([EstMembre])
def vue_liste_evenements_membre(request):
    """
    GET /api/membre/evenements/
    Événements non terminés que le membre peut consulter et confirmer
    depuis son espace, avec son propre statut de confirmation le cas
    échéant (contrairement à vue_liste_evenements, réservée aux
    contrôleurs/admins pour le scan).
    """
    membre = request.user.membre
    mes_confirmations = {
        c.evenement_id: c.statut
        for c in ConfirmationEvenement.objects.filter(membre=membre).exclude(statut="ANNULE")
    }

    # Un événement RESTREINT n'apparaît que pour les membres ciblés ;
    # les autres modes (OUVERT/SUR_INSCRIPTION) restent visibles à tous.
    evenements = (
        Evenement.objects.filter(est_termine=False)
        .exclude(Q(mode_inscription="RESTREINT") & ~Q(membres_cibles=membre))
        .order_by("-id_evenement")[:20]
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
            "seances": [
                {"id_seance": s.id_seance, "numero_ordre": s.numero_ordre, "date_seance": s.date_seance}
                for s in e.seances.order_by("numero_ordre")
            ],
            "mon_statut": mes_confirmations.get(e.id_evenement),
        }
        for e in evenements
    ])


@api_view(["POST"])
@permission_classes([EstMembre])
def vue_confirmer_evenement(request, id_evenement):
    """
    POST /api/evenement/<id>/confirmer/
    Le membre confirme son intention de venir.
    """
    try:
        evenement = Evenement.objects.get(pk=id_evenement)
    except Evenement.DoesNotExist:
        return Response(
            {"erreur": "Événement introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if evenement.est_termine:
        return Response(
            {"erreur": "Cet événement est terminé"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    membre = request.user.membre
    nb_confirmes = ConfirmationEvenement.objects.filter(
        evenement=evenement, statut="CONFIRME"
    ).count()

    # Si capacité max atteinte → liste d'attente
    statut_confirmation = "CONFIRME"
    if evenement.capacite_max and nb_confirmes >= evenement.capacite_max:
        statut_confirmation = "LISTE_ATTENTE"

    confirmation, created = ConfirmationEvenement.objects.get_or_create(
        membre=membre,
        evenement=evenement,
        defaults={"statut": statut_confirmation},
    )

    if not created:
        if confirmation.statut == "ANNULE":
            confirmation.statut = statut_confirmation
            confirmation.save(update_fields=["statut"])
            return Response({
                "message": "Confirmation réactivée",
                "statut": confirmation.statut,
            })
        return Response(
            {"message": "Déjà confirmé", "statut": confirmation.statut},
        )

    return Response({
        "message": "Présence confirmée" if statut_confirmation == "CONFIRME" else "Placé en liste d'attente",
        "statut": statut_confirmation,
        "nb_confirmes": nb_confirmes + (1 if statut_confirmation == "CONFIRME" else 0),
    }, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([EstMembre])
def vue_annuler_confirmation(request, id_evenement):
    """
    DELETE /api/evenement/<id>/confirmer/
    Le membre annule sa confirmation.
    """
    try:
        confirmation = ConfirmationEvenement.objects.get(
            membre=request.user.membre,
            evenement_id=id_evenement,
        )
    except ConfirmationEvenement.DoesNotExist:
        return Response(
            {"erreur": "Aucune confirmation trouvée"},
            status=status.HTTP_404_NOT_FOUND,
        )

    confirmation.statut = "ANNULE"
    confirmation.save(update_fields=["statut"])

    return Response({"message": "Confirmation annulée"})


@api_view(["GET"])
@permission_classes([APermission("GERER_EVENEMENTS")])
def vue_confirmations_evenement(request, id_evenement):
    """
    GET /api/admin/evenement/<id>/confirmations/
    Liste des membres ayant confirmé leur présence.
    """
    try:
        evenement = Evenement.objects.get(pk=id_evenement)
    except Evenement.DoesNotExist:
        return Response(
            {"erreur": "Événement introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    confirmations = ConfirmationEvenement.objects.filter(
        evenement=evenement
    ).select_related("membre__compte")

    return Response({
        "evenement": evenement.titre,
        "nb_confirmes": confirmations.filter(statut="CONFIRME").count(),
        "nb_liste_attente": confirmations.filter(statut="LISTE_ATTENTE").count(),
        "capacite_max": evenement.capacite_max,
        "confirmations": [
            {
                "id_membre": c.membre.id_membre,
                "nom": c.membre.compte.nom,
                "prenom": c.membre.compte.prenom,
                "statut": c.statut,
                "date_confirmation": c.date_confirmation.isoformat(),
            }
            for c in confirmations
        ],
    })


# =========================================================================
# INVITÉS EXTERNES
# =========================================================================

@api_view(["POST"])
@permission_classes([APermission("GERER_EVENEMENTS")])
def vue_ajouter_invite_externe(request, id_evenement):
    """POST /api/admin/evenement/<id>/invite/"""
    try:
        evenement = Evenement.objects.get(pk=id_evenement)
    except Evenement.DoesNotExist:
        return Response(
            {"erreur": "Événement introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    nom = request.data.get("nom", "").strip()
    prenom = request.data.get("prenom", "").strip()

    if not nom or not prenom:
        return Response(
            {"erreur": "nom et prenom sont requis"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    invite = InviteExterne.objects.create(
        nom=nom,
        prenom=prenom,
        telephone=request.data.get("telephone"),
        organisation=request.data.get("organisation"),
        evenement=evenement,
    )

    return Response({
        "message": "Invité ajouté",
        "id_invite": invite.id_invite,
    }, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([APermission("IMPORTER_DONNEES")])
@parser_classes([MultiPartParser, FormParser])
def vue_importer_invites_excel(request, id_evenement):
    """
    POST /api/admin/evenement/<id>/importer-invites/
    Colonnes : nom, prenom, telephone (opt), organisation (opt)
    """
    try:
        evenement = Evenement.objects.get(pk=id_evenement)
    except Evenement.DoesNotExist:
        return Response(
            {"erreur": "Événement introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    fichier = request.FILES.get("fichier")
    if not fichier:
        return Response(
            {"erreur": "Aucun fichier fourni"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        classeur = openpyxl.load_workbook(fichier, read_only=True)
    except Exception:
        return Response(
            {"erreur": "Fichier Excel illisible"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    feuille = classeur.active
    crees = 0
    erreurs = []

    for i, ligne in enumerate(feuille.iter_rows(min_row=2, values_only=True), start=2):
        if len(ligne) < 2:
            erreurs.append(f"Ligne {i} : colonnes insuffisantes")
            continue

        nom = str(ligne[0] or "").strip()
        prenom = str(ligne[1] or "").strip()

        if not nom or not prenom:
            erreurs.append(f"Ligne {i} : nom ou prénom manquant")
            continue

        InviteExterne.objects.create(
            nom=nom,
            prenom=prenom,
            telephone=str(ligne[2] or "").strip() if len(ligne) > 2 else None,
            organisation=str(ligne[3] or "").strip() if len(ligne) > 3 else None,
            evenement=evenement,
        )
        crees += 1

    classeur.close()

    return Response({
        "message": f"{crees} invités ajoutés",
        "crees": crees,
        "erreurs": erreurs,
    })


# =========================================================================
# SUIVI POST-FORMATION
# =========================================================================

@api_view(["POST"])
@permission_classes([APermission("GERER_SUIVI")])
def vue_creer_suivi(request):
    """
    POST /api/admin/suivi-formation/
    Créer une fiche de suivi post-formation.
    """
    id_participant = request.data.get("id_participant")
    id_cohorte = request.data.get("id_cohorte")

    if not id_participant or not id_cohorte:
        return Response(
            {"erreur": "id_participant et id_cohorte sont requis"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        participant = Participant.objects.get(pk=id_participant)
        cohorte = Cohorte.objects.get(pk=id_cohorte)
    except (Participant.DoesNotExist, Cohorte.DoesNotExist):
        return Response(
            {"erreur": "Participant ou cohorte introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    suivi = SuiviPostFormation.objects.create(
        participant=participant,
        cohorte=cohorte,
        date_suivi=request.data.get("date_suivi", timezone.now().date()),
        effectue_par=request.user,
        moyen_contact=request.data.get("moyen_contact", "APPEL"),
        kit_remis=request.data.get("kit_remis", False),
        certificat_emis=request.data.get("certificat_emis", False),
        activite_lancee=request.data.get("activite_lancee", False),
        type_activite=request.data.get("type_activite"),
        localisation_activite=request.data.get("localisation_activite"),
        difficultes=request.data.get("difficultes"),
        niveau_satisfaction=request.data.get("niveau_satisfaction"),
        recommandations=request.data.get("recommandations"),
        statut_global=request.data.get("statut_global", "EN_COURS"),
    )

    return Response({
        "message": "Fiche de suivi créée",
        "id_suivi": suivi.id_suivi,
    }, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([APermission("GERER_SUIVI")])
def vue_liste_suivis_cohorte(request, id_cohorte):
    """
    GET /api/admin/cohorte/<id>/suivis/
    Tableau de suivi post-formation pour une cohorte.
    """
    try:
        cohorte = Cohorte.objects.get(pk=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response(
            {"erreur": "Cohorte introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Tous les participants de cette cohorte avec leurs suivis
    inscriptions = InscriptionCohorte.objects.filter(
        cohorte=cohorte
    ).select_related("participant")

    resultats = []
    for inscription in inscriptions:
        participant = inscription.participant
        suivis = SuiviPostFormation.objects.filter(
            participant=participant, cohorte=cohorte
        ).order_by("-date_suivi")

        dernier_suivi = suivis.first()
        resultats.append({
            "id_participant": participant.id_participant,
            "nom": participant.nom,
            "prenom": participant.prenom,
            "telephone": participant.telephone,
            "nb_suivis": suivis.count(),
            "dernier_suivi": {
                "date": str(dernier_suivi.date_suivi),
                "statut": dernier_suivi.statut_global,
                "kit_remis": dernier_suivi.kit_remis,
                "certificat_emis": dernier_suivi.certificat_emis,
                "activite_lancee": dernier_suivi.activite_lancee,
            } if dernier_suivi else None,
        })

    return Response({
        "cohorte": cohorte.code_cohorte,
        "formation": cohorte.formation.titre,
        "duree_suivi_jours": cohorte.duree_suivi_jours,
        "participants": resultats,
    })


@api_view(["GET"])
@permission_classes([APermission("GERER_SUIVI")])
def vue_historique_suivis_participant(request, id_participant):
    """
    GET /api/admin/participant/<id>/suivis/
    Historique complet des suivis d'un participant.
    """
    try:
        participant = Participant.objects.get(pk=id_participant)
    except Participant.DoesNotExist:
        return Response(
            {"erreur": "Participant introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    suivis = SuiviPostFormation.objects.filter(
        participant=participant
    ).select_related("cohorte__formation", "effectue_par")

    return Response({
        "participant": f"{participant.prenom} {participant.nom}",
        "suivis": [
            {
                "id_suivi": s.id_suivi,
                "cohorte": s.cohorte.code_cohorte,
                "formation": s.cohorte.formation.titre,
                "date_suivi": str(s.date_suivi),
                "effectue_par": f"{s.effectue_par.prenom} {s.effectue_par.nom}",
                "moyen_contact": s.moyen_contact,
                "kit_remis": s.kit_remis,
                "certificat_emis": s.certificat_emis,
                "activite_lancee": s.activite_lancee,
                "type_activite": s.type_activite,
                "difficultes": s.difficultes,
                "niveau_satisfaction": s.niveau_satisfaction,
                "statut_global": s.statut_global,
                "recommandations": s.recommandations,
            }
            for s in suivis
        ],
    })


# =========================================================================
# NOTIFICATIONS
# =========================================================================

@api_view(["GET"])
@permission_classes([EstAuthentifie])
def vue_mes_notifications(request):
    """
    GET /api/notifications/
    Retourne les notifications de l'utilisateur connecté.
    ?non_lues=true pour filtrer les non lues uniquement.
    """
    notifications = Notification.objects.filter(
        compte_destinataire=request.user
    )

    if request.query_params.get("non_lues") == "true":
        notifications = notifications.filter(lu=False)

    return Response({
        "nb_non_lues": Notification.objects.filter(
            compte_destinataire=request.user, lu=False
        ).count(),
        "notifications": [
            {
                "id_notification": n.id_notification,
                "titre": n.titre,
                "corps": n.corps,
                "type": n.type_notification,
                "lu": n.lu,
                "date_creation": n.date_creation.isoformat(),
                "lien_action": n.lien_action,
            }
            for n in notifications[:50]  # limité à 50
        ],
    })


@api_view(["POST"])
@permission_classes([EstAuthentifie])
def vue_marquer_notification_lue(request, id_notification):
    """POST /api/notifications/<id>/lu/"""
    try:
        notification = Notification.objects.get(
            pk=id_notification, compte_destinataire=request.user
        )
    except Notification.DoesNotExist:
        return Response(
            {"erreur": "Notification introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    notification.lu = True
    notification.save(update_fields=["lu"])
    return Response({"message": "Notification marquée comme lue"})


@api_view(["POST"])
@permission_classes([EstAuthentifie])
def vue_marquer_toutes_lues(request):
    """POST /api/notifications/tout-lu/"""
    Notification.objects.filter(
        compte_destinataire=request.user, lu=False
    ).update(lu=True)
    return Response({"message": "Toutes les notifications marquées comme lues"})


# =========================================================================
# DASHBOARD / STATISTIQUES TEMPS RÉEL
# =========================================================================

@api_view(["GET"])
@permission_classes([APermission("VOIR_RAPPORTS")])
def vue_dashboard_stats(request):
    """
    GET /api/dashboard/stats/
    Statistiques en temps réel pour le tableau de bord admin.
    """
    maintenant = timezone.now()
    aujourdhui = maintenant.date()

    # Membres
    nb_membres_actifs = Membre.objects.filter(statut_adhesion="ACTIF").count()
    nb_membres_total = Membre.objects.count()
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

    # Événements
    evenements_a_venir = Evenement.objects.filter(est_termine=False).count()
    evenements_en_cours = Evenement.objects.filter(
        est_termine=False,
        seances__date_seance__date=aujourdhui,
    ).distinct().count()

    # Formations
    formations_en_cours = Cohorte.objects.filter(statut="EN_COURS").count()
    nb_participants_actifs = InscriptionCohorte.objects.filter(
        cohorte__statut="EN_COURS", statut="INSCRIT"
    ).count()

    # Actions sociales
    actions_en_cours = ActionSociale.objects.filter(statut="EN_COURS").count()
    nb_beneficiaires_total = Beneficiaire.objects.count()

    # Registre d'impact : agrégats cumulatifs (pas juste le "live"
    # ci-dessus), toutes périodes confondues.
    formations_terminees_total = Cohorte.objects.filter(statut="TERMINEE").count()
    kits_distribues_total = (
        DistributionKit.objects.filter(distribue=True).count()
        + InscriptionCohorte.objects.filter(kit_distribue=True).count()
    )
    suivis_total = SuiviPostFormation.objects.count()
    suivis_succes = SuiviPostFormation.objects.filter(statut_global="SUCCES").count()
    taux_reussite_suivi = round(100 * suivis_succes / suivis_total, 1) if suivis_total else None

    # Notifications non lues (pour cet admin)
    nb_notifs_non_lues = Notification.objects.filter(
        compte_destinataire=request.user, lu=False
    ).count()

    # Activité récente (7 derniers jours)
    il_y_a_7_jours = maintenant - timezone.timedelta(days=7)
    nouveaux_membres_7j = Membre.objects.filter(
        date_adhesion__gte=il_y_a_7_jours.date()
    ).count()

    return Response({
        "date_mise_a_jour": maintenant.isoformat(),
        "membres": {
            "actifs": nb_membres_actifs,
            "total": nb_membres_total,
            "nouveaux_7j": nouveaux_membres_7j,
        },
        "evenements": {
            "a_venir": evenements_a_venir,
            "en_cours_aujourdhui": evenements_en_cours,
        },
        "formations": {
            "cohortes_en_cours": formations_en_cours,
            "participants_actifs": nb_participants_actifs,
        },
        "actions_sociales": {
            "en_cours": actions_en_cours,
            "beneficiaires_total": nb_beneficiaires_total,
        },
        "notifications_non_lues": nb_notifs_non_lues,
        "repartition_par_fonction": repartition_par_fonction,
        "impact": {
            "beneficiaires_aides_total": nb_beneficiaires_total,
            "formations_terminees_total": formations_terminees_total,
            "kits_distribues_total": kits_distribues_total,
            "taux_reussite_suivi": taux_reussite_suivi,
        },
    })


# =========================================================================
# ANNUAIRE INTERNE
# =========================================================================

@api_view(["GET"])
@permission_classes([EstAuthentifie])
def vue_annuaire(request):
    """
    GET /api/annuaire/?fonction=&q=
    Répertoire interne des membres, accessible à TOUT compte connecté
    (admin, contrôleur ou membre) - outil de réseautage, jamais public.
    Volontairement allégé par rapport à /api/admin/membres/ : pas
    d'email, téléphone, numéro d'adhérent ni statut de carte (données
    sensibles réservées à la gestion admin).
    """
    membres = Membre.objects.select_related("compte", "section").filter(
        statut_adhesion="ACTIF"
    )

    fonction = request.query_params.get("fonction")
    if fonction:
        membres = membres.filter(fonction_association=fonction)

    q = request.query_params.get("q", "").strip()
    if q:
        membres = membres.filter(
            Q(compte__nom__icontains=q) | Q(compte__prenom__icontains=q)
        )

    membres = membres.order_by("compte__nom", "compte__prenom")
    libelles_fonction = dict(Membre.FONCTION_CHOICES)

    return Response([
        {
            "id_membre": m.id_membre,
            "nom": m.compte.nom,
            "prenom": m.compte.prenom,
            "photo": m.photo.url if m.photo else None,
            "fonction_association": m.fonction_association,
            "fonction_association_libelle": libelles_fonction.get(m.fonction_association, m.fonction_association),
            "est_direction": m.fonction_association in Membre.FONCTIONS_DIRECTION,
            "section": m.section.nom_section if m.section else None,
        }
        for m in membres
    ])


# =========================================================================
# GOUVERNANCE : comptes-rendus de réunions du bureau exécutif.
# Lecture ouverte à tout compte connecté ; écriture réservée à
# GERER_GOUVERNANCE (ou super admin, via APermission).
# =========================================================================

def _serialiser_compte_rendu(cr):
    return {
        "id_compte_rendu": cr.id_compte_rendu,
        "titre": cr.titre,
        "date_reunion": cr.date_reunion,
        "contenu": cr.contenu,
        "decisions": cr.decisions,
        "cree_par": f"{cr.cree_par.prenom} {cr.cree_par.nom}",
        "date_creation": cr.date_creation,
    }


@api_view(["GET", "POST"])
@permission_classes([EstAuthentifie])
def vue_comptes_rendus(request):
    """
    GET /api/gouvernance/comptes-rendus/ - lecture, tout compte connecté.
    POST /api/gouvernance/comptes-rendus/ - création, GERER_GOUVERNANCE uniquement.
    """
    if request.method == "GET":
        comptes_rendus = CompteRendu.objects.select_related("cree_par").all()
        return Response([_serialiser_compte_rendu(cr) for cr in comptes_rendus])

    if not compte_a_permission(request.user, "GERER_GOUVERNANCE"):
        return Response({"erreur": "Permission refusée"}, status=status.HTTP_403_FORBIDDEN)

    donnees = request.data
    for champ in ["titre", "date_reunion", "contenu"]:
        if not donnees.get(champ):
            return Response({"erreur": f"Le champ '{champ}' est requis"}, status=status.HTTP_400_BAD_REQUEST)

    compte_rendu = CompteRendu.objects.create(
        titre=donnees["titre"],
        date_reunion=donnees["date_reunion"],
        contenu=donnees["contenu"],
        decisions=donnees.get("decisions", ""),
        cree_par=request.user,
    )

    JournalAudit.objects.create(
        type_action="CREATION_COMPTE_RENDU",
        description=f"Compte-rendu créé : {compte_rendu.titre} ({compte_rendu.date_reunion})",
        compte_auteur=request.user,
    )

    return Response(_serialiser_compte_rendu(compte_rendu), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([EstAuthentifie])
def vue_detail_compte_rendu(request, id_compte_rendu):
    """
    GET /api/gouvernance/comptes-rendus/<id>/ - lecture, tout compte connecté.
    PATCH/DELETE - GERER_GOUVERNANCE uniquement.
    """
    try:
        compte_rendu = CompteRendu.objects.select_related("cree_par").get(pk=id_compte_rendu)
    except CompteRendu.DoesNotExist:
        return Response({"erreur": "Compte-rendu introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(_serialiser_compte_rendu(compte_rendu))

    if not compte_a_permission(request.user, "GERER_GOUVERNANCE"):
        return Response({"erreur": "Permission refusée"}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "DELETE":
        titre = compte_rendu.titre
        compte_rendu.delete()
        JournalAudit.objects.create(
            type_action="SUPPRESSION_COMPTE_RENDU",
            description=f"Compte-rendu supprimé : {titre}",
            compte_auteur=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    donnees = request.data
    for champ in ["titre", "date_reunion", "contenu", "decisions"]:
        if champ in donnees:
            setattr(compte_rendu, champ, donnees[champ])
    compte_rendu.save()

    JournalAudit.objects.create(
        type_action="MODIFICATION_COMPTE_RENDU",
        description=f"Compte-rendu modifié : {compte_rendu.titre}",
        compte_auteur=request.user,
    )

    return Response(_serialiser_compte_rendu(compte_rendu))


# =========================================================================
# IMPORT EXCEL POUR MEMBRES
# =========================================================================

@api_view(["POST"])
@permission_classes([APermission("IMPORTER_DONNEES")])
@parser_classes([MultiPartParser, FormParser])
def vue_importer_membres_excel(request):
    """
    POST /api/admin/importer-membres/
    Importe une liste de membres depuis Excel.
    Colonnes : nom, prenom, email, telephone (opt), section_id (opt),
    fonction_association (opt, code parmi Membre.FONCTION_CHOICES -
    "MEMBRE_ACTIF" par défaut si absent/invalide)
    """
    from .models import Section
    from django.contrib.auth.hashers import make_password

    fichier = request.FILES.get("fichier")
    if not fichier:
        return Response(
            {"erreur": "Aucun fichier fourni"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        classeur = openpyxl.load_workbook(fichier, read_only=True)
    except Exception:
        return Response(
            {"erreur": "Fichier Excel illisible"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    feuille = classeur.active
    crees = 0
    doublons = 0
    erreurs = []

    for i, ligne in enumerate(feuille.iter_rows(min_row=2, values_only=True), start=2):
        if len(ligne) < 3:
            erreurs.append(f"Ligne {i} : colonnes insuffisantes (nom, prenom, email requis)")
            continue

        nom = str(ligne[0] or "").strip()
        prenom = str(ligne[1] or "").strip()
        email = str(ligne[2] or "").strip()

        if not nom or not prenom or not email:
            erreurs.append(f"Ligne {i} : nom, prénom ou email manquant")
            continue

        if Compte.objects.filter(email=email).exists():
            doublons += 1
            continue

        telephone = str(ligne[3] or "").strip() if len(ligne) > 3 else None
        id_section = int(ligne[4]) if len(ligne) > 4 and ligne[4] else None

        section = None
        if id_section:
            try:
                section = Section.objects.get(pk=id_section)
            except Section.DoesNotExist:
                erreurs.append(f"Ligne {i} : section {id_section} introuvable")
                continue

        fonction_brute = str(ligne[5] or "").strip().upper() if len(ligne) > 5 else ""
        fonction_association = fonction_brute if fonction_brute in dict(Membre.FONCTION_CHOICES) else "MEMBRE_ACTIF"

        # Mot de passe temporaire ALÉATOIRE, jamais communiqué : l'ancien
        # format (AP2A- + 3 lettres du prénom + 3 du nom) se devinait à
        # partir de l'annuaire. Le membre reçoit ses vrais identifiants
        # via "Renvoyer les identifiants", qui en régénère un.
        mot_de_passe_temp = generer_mot_de_passe_temporaire()

        compte = Compte.objects.create(
            email=email,
            mot_de_passe_hash=make_password(mot_de_passe_temp),
            nom=nom,
            prenom=prenom,
            telephone=telephone,
            doit_changer_mot_de_passe=True,
        )

        import uuid as uuid_lib
        from .utils import generer_signature

        uuid_carte = uuid_lib.uuid4()
        signature, version = generer_signature(str(uuid_carte))

        membre = Membre.objects.create(
            compte=compte,
            section=section,
            fonction_association=fonction_association,
            date_adhesion=timezone.now().date(),
            numero_adherent=f"AP2A-{compte.id_compte:06d}",
        )

        from .models import Carte
        Carte.objects.create(
            uuid=uuid_carte,
            type_carte="QR",
            membre=membre,
            id_version_cle=version,
        )

        crees += 1

    classeur.close()

    JournalAudit.objects.create(
        type_action="IMPORT_MEMBRES",
        description=f"Import Excel : {crees} membres créés, {doublons} doublons, {len(erreurs)} erreurs",
        compte_auteur=request.user,
    )

    return Response({
        "message": f"Import terminé : {crees} membres créés",
        "crees": crees,
        "doublons": doublons,
        "erreurs": erreurs,
    })


# =========================================================================
# CONFIGURATION DE L'ASSOCIATION
# =========================================================================

@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_config_association(request):
    """GET /api/admin/config/"""
    config = ConfigAssociation.charger()
    return Response({
        "cotisation_active": config.cotisation_active,
        "montant_cotisation_annuel": str(config.montant_cotisation_annuel) if config.montant_cotisation_annuel else None,
        "seuil_certification_defaut": config.seuil_certification_defaut,
        "duree_suivi_defaut_jours": config.duree_suivi_defaut_jours,
        "nom_association": config.nom_association,
        "slogan": config.slogan,
        "email_contact": config.email_contact,
        "telephone_contact": config.telephone_contact,
        "logo": request.build_absolute_uri(config.logo.url) if config.logo else None,
    })


@api_view(["PUT"])
@permission_classes([EstSuperAdmin])
def vue_modifier_config(request):
    """PUT /api/admin/config/"""
    config = ConfigAssociation.charger()

    champs_modifiables = [
        "cotisation_active", "montant_cotisation_annuel",
        "seuil_certification_defaut", "duree_suivi_defaut_jours",
        "nom_association", "slogan", "email_contact", "telephone_contact",
    ]

    for champ in champs_modifiables:
        if champ in request.data:
            setattr(config, champ, request.data[champ])

    if "logo" in request.FILES:
        config.logo = request.FILES["logo"]

    config.save()

    JournalAudit.objects.create(
        type_action="MODIFICATION_CONFIG",
        description="Configuration de l'association modifiée",
        compte_auteur=request.user,
    )

    return Response({"message": "Configuration mise à jour"})


# =========================================================================
# CERTIFICATS PDF & VÉRIFICATION
# =========================================================================

@api_view(["GET"])
@permission_classes([EstAuthentifie])
def vue_telecharger_certificat_participant(request, id_cohorte, id_participant):
    """
    GET /api/cohorte/<id_cohorte>/certificat/<id_participant>/
    Génère et renvoie le PDF du certificat pour un participant.
    Accessible aux gestionnaires des formations, ou au participant
    lui-même (via son compte membre lié) - jamais à un autre membre qui
    ferait varier les identifiants dans l'URL.
    """
    from django.http import HttpResponse
    from .certificats import generer_certificat_pdf

    try:
        cohorte = Cohorte.objects.select_related("formation").get(pk=id_cohorte)
        participant = Participant.objects.select_related("membre").get(pk=id_participant)
    except (Cohorte.DoesNotExist, Participant.DoesNotExist):
        return Response(
            {"erreur": "Cohorte ou participant introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    est_gestionnaire = compte_a_permission(request.user, "GERER_FORMATIONS")
    est_titulaire = participant.membre is not None and participant.membre.compte_id == request.user.id_compte
    if not (est_gestionnaire or est_titulaire):
        # 404 plutôt que 403 : ne pas confirmer l'existence du couple
        # cohorte/participant à quelqu'un qui n'y a pas droit.
        return Response(
            {"erreur": "Cohorte ou participant introuvable"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not InscriptionCohorte.objects.filter(cohorte=cohorte, participant=participant).exists():
        return Response(
            {"erreur": "Ce participant n'est pas inscrit à cette cohorte"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Calcul du taux de présence
    seances_total = SeanceCohorte.objects.filter(cohorte=cohorte).count()
    if seances_total == 0:
        return Response(
            {"erreur": "Aucune séance n'a été planifiée pour cette cohorte"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    presences_validees = PresenceCohorte.objects.filter(
        participant=participant,
        seance_cohorte__cohorte=cohorte,
    ).count()

    taux = (presences_validees / seances_total) * 100
    seuil = cohorte.seuil_certification or 75

    if taux < seuil and not est_gestionnaire:
        return Response(
            {
                "erreur": f"Taux de présence insuffisant ({taux:.0f}% < {seuil}% requis)",
                "taux_actuel": taux,
                "seuil_requis": seuil,
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    pdf_bytes = generer_certificat_pdf(participant, cohorte, taux)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"Certificat_{cohorte.code_cohorte}_{participant.nom}_{participant.prenom}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api_view(["GET"])
@permission_classes([AllowAny])
def vue_verifier_certificat_public(request, numero_certificat):
    """
    GET /api/public/verifier-certificat/<numero_certificat>/
    Point de vérification publique accessible en scannant le QR code du certificat.
    """
    # Format: CERT-<CODE_COHORTE>-<ID_PARTICIPANT>
    parties = numero_certificat.split("-")
    if len(parties) < 3 or parties[0] != "CERT":
        return Response(
            {"valide": False, "erreur": "Format de certificat invalide"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        id_participant = int(parties[-1])
        code_cohorte = "-".join(parties[1:-1])
        cohorte = Cohorte.objects.select_related("formation").get(code_cohorte=code_cohorte)
        participant = Participant.objects.get(pk=id_participant)
    except (ValueError, Cohorte.DoesNotExist, Participant.DoesNotExist):
        return Response(
            {"valide": False, "erreur": "Certificat introuvable dans le registre AP2A"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response({
        "valide": True,
        "numero_certificat": numero_certificat,
        "participant": f"{participant.prenom} {participant.nom}",
        "formation": cohorte.formation.titre,
        "cohorte": cohorte.code_cohorte,
        "date_debut": str(cohorte.date_debut),
        "date_fin": str(cohorte.date_fin) if cohorte.date_fin else None,
        "association": "AP2A",
    })


# =========================================================================
# BADGES PDF (Lot et individuel)
# =========================================================================

@api_view(["POST", "GET"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_telecharger_badges_cohorte_lot(request, id_cohorte):
    """
    POST /api/cohorte/<id_cohorte>/badges/pdf/
    Génère une planche A4 (PDF) contenant les badges d'accès pour toute la cohorte
    ou pour les participants spécifiés dans `participants_ids`.
    Corps optionnel : {"inclure_photo": true|false} (défaut : true).
    """
    from .badges import generer_planche_badges_pdf
    from django.http import HttpResponse

    try:
        cohorte = Cohorte.objects.select_related("formation").get(pk=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    inscriptions = InscriptionCohorte.objects.filter(cohorte=cohorte).select_related("participant", "cohorte__formation")

    participants_ids = request.data.get("participants_ids", []) if request.method == "POST" else []
    if participants_ids:
        inscriptions = inscriptions.filter(participant_id__in=participants_ids)

    if not inscriptions.exists():
        return Response({"erreur": "Aucun participant sélectionné pour la génération des badges"}, status=status.HTTP_400_BAD_REQUEST)

    inclure_photo = str(request.data.get("inclure_photo", True)).lower() not in ("false", "0")
    pdf_bytes = generer_planche_badges_pdf(inscriptions, inclure_photo=inclure_photo)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Badges_{cohorte.code_cohorte}.pdf"'
    return response


@api_view(["GET"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_telecharger_badge_participant(request, id_cohorte, id_participant):
    """
    GET /api/cohorte/<id_cohorte>/badge/<id_participant>/
    Génère le badge individuel PDF pour un participant donné.
    Paramètre optionnel : ?inclure_photo=true|false (défaut : true).
    """
    from .badges import generer_badge_unique_pdf
    from django.http import HttpResponse

    try:
        cohorte = Cohorte.objects.select_related("formation").get(pk=id_cohorte)
        participant = Participant.objects.get(pk=id_participant)
    except (Cohorte.DoesNotExist, Participant.DoesNotExist):
        return Response({"erreur": "Cohorte ou participant introuvable"}, status=status.HTTP_404_NOT_FOUND)

    inclure_photo = request.query_params.get("inclure_photo", "true").lower() not in ("false", "0")
    pdf_bytes = generer_badge_unique_pdf(participant, cohorte, inclure_photo=inclure_photo)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Badge_{cohorte.code_cohorte}_{participant.nom}_{participant.prenom}.pdf"'
    return response


# =========================================================================
# CERTIFICATS & KITS EN LOT
# =========================================================================

@api_view(["POST"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_telecharger_certificats_lot(request, id_cohorte):
    """
    POST /api/cohorte/<id_cohorte>/certificats/pdf-lot/
    Génère un fichier PDF multipage contenant les certificats des participants éligibles sélectionnés.
    """
    from .certificats import generer_certificats_lot_pdf
    from django.http import HttpResponse

    try:
        cohorte = Cohorte.objects.select_related("formation").get(pk=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    inscriptions = InscriptionCohorte.objects.filter(cohorte=cohorte).select_related("participant", "cohorte__formation")
    
    participants_ids = request.data.get("participants_ids", [])
    if participants_ids:
        inscriptions = inscriptions.filter(participant_id__in=participants_ids)

    if not inscriptions.exists():
        return Response({"erreur": "Aucun participant sélectionné pour les certificats"}, status=status.HTTP_400_BAD_REQUEST)

    # Marquer les certificats comme délivrés
    inscriptions.update(certificat_delivre=True, date_emission_certificat=timezone.now())

    pdf_bytes = generer_certificats_lot_pdf(inscriptions)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Certificats_{cohorte.code_cohorte}.pdf"'
    return response


@api_view(["POST"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_valider_kits_lot(request, id_cohorte):
    """
    POST /api/cohorte/<id_cohorte>/kits/valider-lot/
    Valide ou retire la distribution des kits en lot.
    Corps : { "participants_ids": [1, 2, 3], "kit_distribue": true }

    Volontairement PAS bloqué par le verrouillage de la cohorte (une fois
    terminée) : marquer un kit comme remis est une action de suivi qui a
    justement lieu APRÈS la fin de la session, pas une modification de son
    contenu ou de son déroulement.
    """
    try:
        cohorte = Cohorte.objects.get(pk=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    participants_ids = request.data.get("participants_ids", [])
    kit_distribue = request.data.get("kit_distribue", True)

    inscriptions = InscriptionCohorte.objects.filter(cohorte=cohorte)
    if participants_ids:
        inscriptions = inscriptions.filter(participant_id__in=participants_ids)

    if kit_distribue:
        inscriptions.update(
            kit_distribue=True,
            date_distribution_kit=timezone.now(),
            remis_par=request.user,
        )
    else:
        inscriptions.update(
            kit_distribue=False,
            date_distribution_kit=None,
            remis_par=None,
        )

    JournalAudit.objects.create(
        type_action="VALIDATION_KITS_LOT",
        description=f"Remise de kit mise à jour pour {inscriptions.count()} participant(s) de la cohorte {cohorte.code_cohorte}",
        compte_auteur=request.user,
    )

    return Response({
        "success": True,
        "nb_mis_a_jour": inscriptions.count(),
        "kit_distribue": kit_distribue,
    })


# =========================================================================
# ACTIONS SOCIALES : DONS & VISITES MÉDICALES
# =========================================================================

@api_view(["POST"])
@permission_classes([APermission("GERER_ACTIONS_SOCIALES")])
def vue_basculer_don_recu(request, id_action, id_participation):
    """
    POST /api/admin/action-sociale/<id_action>/participation/<id_participation>/basculer-don/
    Bascule l'état don_recu (Reçu / Non reçu) pour un bénéficiaire.
    """
    try:
        participation = ParticipationAction.objects.select_related("beneficiaire", "action").get(
            pk=id_participation, action_id=id_action
        )
    except ParticipationAction.DoesNotExist:
        return Response({"erreur": "Participation introuvable"}, status=status.HTTP_404_NOT_FOUND)

    participation.don_recu = not participation.don_recu
    if participation.don_recu:
        participation.date_reception_don = timezone.now()
        participation.statut = "TERMINE"
    else:
        participation.date_reception_don = None

    participation.save()

    JournalAudit.objects.create(
        type_action="DON_RECU_MODIFIE",
        description=f"Statut don reçu = {participation.don_recu} pour {participation.beneficiaire.nom} ({participation.action.titre})",
        compte_auteur=request.user,
    )

    return Response({
        "id_participation": participation.id_participation,
        "don_recu": participation.don_recu,
        "date_reception_don": str(participation.date_reception_don) if participation.date_reception_don else None,
        "statut": participation.statut,
    })


@api_view(["POST", "PATCH"])
@permission_classes([APermission("GERER_ACTIONS_SOCIALES")])
def vue_mettre_a_jour_suivi_medical(request, id_action, id_participation):
    """
    POST/PATCH /api/admin/action-sociale/<id_action>/participation/<id_participation>/suivi-medical/
    Met à jour le suivi de consultation / visite médicale et planification de traitement.
    """
    try:
        participation = ParticipationAction.objects.get(pk=id_participation, action_id=id_action)
    except ParticipationAction.DoesNotExist:
        return Response({"erreur": "Participation introuvable"}, status=status.HTTP_404_NOT_FOUND)

    detail, _ = DetailMedical.objects.get_or_create(
        participation=participation,
        defaults={
            "date_consultation": timezone.localdate(),
            "type_soin": "CONSULTATION",
        },
    )

    for champ in ["a_ete_visite", "date_visite", "necessite_traitement", "description_besoin_traitement", "traitement_effectue", "date_traitement", "notes_suivi", "medecin_referent", "type_soin"]:
        if champ in request.data:
            setattr(detail, champ, request.data[champ])

    detail.save()

    if detail.traitement_effectue:
        participation.statut = "TERMINE"
        participation.save()

    return Response({
        "id_participation": participation.id_participation,
        "a_ete_visite": detail.a_ete_visite,
        "date_visite": str(detail.date_visite) if detail.date_visite else None,
        "necessite_traitement": detail.necessite_traitement,
        "description_besoin_traitement": detail.description_besoin_traitement,
        "traitement_effectue": detail.traitement_effectue,
        "date_traitement": str(detail.date_traitement) if detail.date_traitement else None,
        "notes_suivi": detail.notes_suivi,
    })


# =========================================================================
# ACTER / PROGRAMMER (Formations & Événements avec notifications auto)
# =========================================================================

@api_view(["POST"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_acter_programme_cohorte(request, id_cohorte):
    """
    POST /api/admin/cohorte/<id_cohorte>/acter-programme/
    Passe la cohorte du statut BROUILLON à PROGRAMMEE, et notifie les membres ciblés.
    """
    try:
        cohorte = Cohorte.objects.select_related("formation").get(pk=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if cohorte.statut != "BROUILLON":
        return Response(
            {"erreur": "Cette cohorte a déjà été lancée (ou n'est plus en planification)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cohorte.statut = "PROGRAMMEE"
    cohorte.save()

    # Déterminer les membres à notifier
    if cohorte.public_cible_type == "SPECIFIQUE":
        membres = cohorte.membres_cibles.filter(compte__statut_compte="ACTIF").select_related("compte")
    else:
        membres = Membre.objects.filter(compte__statut_compte="ACTIF").select_related("compte")

    titre_notif = f"Nouvelle formation : {cohorte.formation.titre}"
    corps_notif = (
        f"La session {cohorte.code_cohorte} est officiellement programmée "
        f"du {cohorte.date_debut:%d/%m/%Y} au {cohorte.date_fin:%d/%m/%Y} à {cohorte.lieu or 'AP2A'}. "
        f"Inscrivez-vous dès maintenant !"
    )

    emails_to_send = []
    for m in membres:
        Notification.objects.create(
            compte_destinataire=m.compte,
            titre=titre_notif,
            corps=corps_notif,
            type_notification="INFO",
            lien_action=f"/formations/{cohorte.formation.id_formation}",
        )
        if est_email_reel(m.compte.email):
            emails_to_send.append(m.compte.email)

    # Envoi d'email groupé, en arrière-plan (ne doit jamais retarder la
    # réponse de "Lancer la formation" - voir envoyer_email_arriere_plan).
    if emails_to_send:
        envoyer_email_arriere_plan(
            sujet=f"[AP2A] {titre_notif}",
            message=f"Bonjour,\n\n{corps_notif}\n\nRetrouvez tous les détails dans votre espace adhérent AP2A.\n\nCordialement,\nL'équipe AP2A",
            destinataires=emails_to_send,
        )

    JournalAudit.objects.create(
        type_action="ACTER_PROGRAMME_COHORTE",
        description=f"Cohorte programmée et notifiée à {membres.count()} membres : {cohorte.code_cohorte}",
        compte_auteur=request.user,
    )

    return Response({
        "success": True,
        "statut": cohorte.statut,
        "membres_notifies": membres.count(),
    })


@api_view(["POST"])
@permission_classes([APermission("GERER_EVENEMENTS")])
def vue_acter_programme_evenement(request, id_evenement):
    """
    POST /api/admin/evenement/<id_evenement>/acter-programme/
    Passe l'événement du statut BROUILLON à PROGRAMME, et notifie les membres concernés.
    """
    try:
        evenement = Evenement.objects.get(pk=id_evenement)
    except Evenement.DoesNotExist:
        return Response({"erreur": "Événement introuvable"}, status=status.HTTP_404_NOT_FOUND)

    evenement.statut = "PROGRAMME"
    evenement.save()

    if evenement.mode_inscription == "RESTREINT":
        membres = evenement.membres_cibles.filter(compte__statut_compte="ACTIF").select_related("compte")
    else:
        membres = Membre.objects.filter(compte__statut_compte="ACTIF").select_related("compte")

    titre_notif = f"Événement programmé : {evenement.titre}"
    corps_notif = (
        f"L'événement {evenement.titre} est programmé à {evenement.lieu}. "
        f"Consultez les détails et confirmez votre présence dans votre espace adhérent."
    )

    emails_to_send = []
    for m in membres:
        Notification.objects.create(
            compte_destinataire=m.compte,
            titre=titre_notif,
            corps=corps_notif,
            type_notification="INFO",
            lien_action=f"/evenements/{evenement.id_evenement}",
        )
        if est_email_reel(m.compte.email):
            emails_to_send.append(m.compte.email)

    if emails_to_send:
        envoyer_email_arriere_plan(
            sujet=f"[AP2A] {titre_notif}",
            message=f"Bonjour,\n\n{corps_notif}\n\nCordialement,\nL'association AP2A",
            destinataires=emails_to_send,
        )

    JournalAudit.objects.create(
        type_action="ACTER_PROGRAMME_EVENEMENT",
        description=f"Événement acté et notifié à {membres.count()} membres : {evenement.titre}",
        compte_auteur=request.user,
    )

    return Response({
        "success": True,
        "statut": evenement.statut,
        "membres_notifies": membres.count(),
    })


# -------------------------------------------------------------------------
# RENVOI DES IDENTIFIANTS MEMBRE EN 1 CLIC
# -------------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([APermission("GERER_MEMBRES")])
def vue_renvoyer_identifiants_membre(request, id_membre):
    """
    POST /api/admin/membre/<id_membre>/renvoyer-identifiants/
    Corps optionnel : {"email": "..."}

    Génère un nouveau mot de passe temporaire et l'envoie à l'adhérent par email.

    Si le membre n'a pas encore d'adresse email réelle (compte créé sans
    email -> placeholder @membre.ap2a.local) et qu'aucun "email" n'est
    fourni dans le corps de la requête, on refuse avec le code
    EMAIL_MANQUANT plutôt que de générer un mot de passe qu'on ne pourra
    remettre à personne : le frontend intercepte ce code pour demander
    l'adresse à l'admin puis rejouer la requête avec "email" renseigné.
    Une fois fourni, l'email est enregistré sur le compte du membre.
    """
    from django.contrib.auth.hashers import make_password
    import secrets

    try:
        membre = Membre.objects.select_related("compte").get(pk=id_membre)
    except Membre.DoesNotExist:
        return Response({"erreur": "Membre introuvable"}, status=status.HTTP_404_NOT_FOUND)

    compte = membre.compte
    email_fourni = (request.data.get("email") or "").strip()

    if email_fourni:
        if Compte.objects.exclude(pk=compte.pk).filter(email__iexact=email_fourni).exists():
            return Response(
                {"erreur": "Cet email est déjà utilisé par un autre compte", "code": "EMAIL_DEJA_UTILISE"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        compte.email = email_fourni
        compte.save(update_fields=["email"])
    elif not est_email_reel(compte.email):
        return Response(
            {
                "erreur": "Ce membre n'a pas d'adresse email enregistrée. Renseignez-en une pour lui envoyer ses identifiants.",
                "code": "EMAIL_MANQUANT",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    nouveau_mdp = generer_mot_de_passe_temporaire()

    compte.mot_de_passe_hash = make_password(nouveau_mdp)
    compte.doit_changer_mot_de_passe = True
    compte.save(update_fields=["mot_de_passe_hash", "doit_changer_mot_de_passe"])

    # Envoi en arrière-plan : un envoi SMTP synchrone (plusieurs
    # secondes, parfois plus depuis un serveur cloud) ferait dépasser
    # le timeout du proxy d'hébergement et ferait échouer toute
    # l'action alors que le mot de passe a déjà été régénéré.
    envoyer_email_arriere_plan(
        sujet="AP2A - Vos identifiants de connexion",
        message=(
            f"Bonjour {compte.prenom} {compte.nom},\n\n"
            f"Voici vos identifiants pour vous connecter à votre espace adhérent AP2A :\n\n"
            f"Identifiant / Email : {compte.email}\n"
            f"Numéro adhérent : {membre.numero_adherent}\n"
            f"Mot de passe temporaire : {nouveau_mdp}\n\n"
            f"Lors de votre première connexion, vous serez invité à choisir votre propre mot de passe personnel.\n\n"
            f"Cordialement,\nL'Association AP2A"
        ),
        destinataires=[compte.email],
    )
    email_envoye = True

    JournalAudit.objects.create(
        type_action="RENVOI_IDENTIFIANTS",
        description=f"Identifiants régénérés pour {compte.prenom} {compte.nom} ({membre.numero_adherent}) - Email envoyé: {email_envoye}",
        compte_auteur=request.user,
    )

    return Response({
        "success": True,
        "numero_adherent": membre.numero_adherent,
        "email": compte.email,
        "mot_de_passe_temporaire": nouveau_mdp,
        "email_envoye": email_envoye,
    })


# =========================================================================
# KITS PÉDAGOGIQUES (module de paramétrage séparé des formations : une
# formation n'implique pas forcément un kit, donc on prépare un kit
# uniquement quand il y en a besoin, et on choisit alors librement à qui
# il est destiné - une formation entière, une cohorte, ou certains
# participants d'une cohorte)
# =========================================================================

def _serialiser_kit(kit):
    return {
        "id_kit": kit.id_kit,
        "nom": kit.nom,
        "contenu": kit.contenu,
        "type_cible": kit.type_cible,
        "formation": (
            {"id_formation": kit.formation.id_formation, "titre": kit.formation.titre}
            if kit.formation else None
        ),
        "cohorte": (
            {"id_cohorte": kit.cohorte.id_cohorte, "code_cohorte": kit.cohorte.code_cohorte}
            if kit.cohorte else None
        ),
        "participants_cibles": [
            {"id_participant": p.id_participant, "nom": p.nom, "prenom": p.prenom}
            for p in kit.participants_cibles.all()
        ],
        "cree_par": f"{kit.cree_par.prenom} {kit.cree_par.nom}" if kit.cree_par else None,
        "date_creation": kit.date_creation,
    }


def _appliquer_cible_kit(kit, donnees):
    """
    Valide et applique le ciblage d'un kit selon type_cible - factorisé
    entre création et modification. Retourne une Response d'erreur si les
    données sont incohérentes, sinon None (le kit est déjà rempli, à
    l'appelant de faire kit.save()).
    """
    type_cible = donnees.get("type_cible")
    if type_cible not in dict(Kit.CIBLE_CHOICES):
        return Response({"erreur": "type_cible invalide"}, status=status.HTTP_400_BAD_REQUEST)

    kit.type_cible = type_cible
    kit.formation = None
    kit.cohorte = None
    kit._participants_a_appliquer = []  # appliqué après save() par l'appelant (M2M)

    if type_cible == Kit.CIBLE_FORMATION:
        id_formation = donnees.get("id_formation")
        try:
            kit.formation = Formation.objects.get(pk=id_formation)
        except (Formation.DoesNotExist, TypeError, ValueError):
            return Response({"erreur": "Formation introuvable"}, status=status.HTTP_404_NOT_FOUND)

    elif type_cible == Kit.CIBLE_COHORTE:
        id_cohorte = donnees.get("id_cohorte")
        try:
            kit.cohorte = Cohorte.objects.get(pk=id_cohorte)
        except (Cohorte.DoesNotExist, TypeError, ValueError):
            return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    elif type_cible == Kit.CIBLE_PARTICIPANTS:
        id_cohorte = donnees.get("id_cohorte")
        participants_ids = donnees.get("participants_ids") or []
        try:
            kit.cohorte = Cohorte.objects.get(pk=id_cohorte)
        except (Cohorte.DoesNotExist, TypeError, ValueError):
            return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)
        if not participants_ids:
            return Response(
                {"erreur": "Sélectionnez au moins un participant"}, status=status.HTTP_400_BAD_REQUEST,
            )
        ids_inscrits = set(
            InscriptionCohorte.objects.filter(
                cohorte=kit.cohorte, participant_id__in=participants_ids,
            ).values_list("participant_id", flat=True)
        )
        if ids_inscrits != set(participants_ids):
            return Response(
                {"erreur": "Certains participants sélectionnés ne sont pas inscrits à cette cohorte"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        kit._participants_a_appliquer = list(ids_inscrits)

    return None


@api_view(["POST"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_creer_kit(request):
    """
    POST /api/admin/kit/
    Corps : {"nom", "contenu", "type_cible": "FORMATION"|"COHORTE"|"PARTICIPANTS",
             "id_formation" (si FORMATION), "id_cohorte" (si COHORTE/PARTICIPANTS),
             "participants_ids" (si PARTICIPANTS)}
    """
    donnees = request.data
    nom = (donnees.get("nom") or "").strip()
    contenu = (donnees.get("contenu") or "").strip()
    if not nom or not contenu:
        return Response({"erreur": "nom et contenu sont requis"}, status=status.HTTP_400_BAD_REQUEST)

    kit = Kit(nom=nom, contenu=contenu, cree_par=request.user)
    erreur = _appliquer_cible_kit(kit, donnees)
    if erreur:
        return erreur

    kit.save()
    if kit.type_cible == Kit.CIBLE_PARTICIPANTS:
        kit.participants_cibles.set(kit._participants_a_appliquer)

    JournalAudit.objects.create(
        type_action="creation_kit",
        description=f"Kit créé : {kit.nom}",
        compte_auteur=request.user,
    )

    return Response(_serialiser_kit(kit), status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_liste_kits(request):
    """
    GET /api/kits/?formation=<id>&cohorte=<id>
    Sans filtre : tous les kits, les plus récents en premier. Avec un
    filtre : seulement les kits explicitement ciblés sur cette formation
    ou cette cohorte (pas de résolution "cohorte appartient à cette
    formation" ici - le module est volontairement simple).
    """
    kits = Kit.objects.select_related("formation", "cohorte", "cree_par").prefetch_related("participants_cibles")

    id_formation = request.query_params.get("formation")
    if id_formation:
        kits = kits.filter(formation_id=id_formation)

    id_cohorte = request.query_params.get("cohorte")
    if id_cohorte:
        kits = kits.filter(cohorte_id=id_cohorte)

    return Response([_serialiser_kit(k) for k in kits])


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_detail_ou_gerer_kit(request, id_kit):
    """
    GET /api/admin/kit/<id>/ - détail
    PATCH /api/admin/kit/<id>/ - modification (nom/contenu/ciblage)
    DELETE /api/admin/kit/<id>/ - suppression
    """
    try:
        kit = Kit.objects.select_related("formation", "cohorte", "cree_par").get(pk=id_kit)
    except Kit.DoesNotExist:
        return Response({"erreur": "Kit introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(_serialiser_kit(kit))

    if request.method == "DELETE":
        nom = kit.nom
        kit.delete()
        JournalAudit.objects.create(
            type_action="suppression_kit",
            description=f"Kit supprimé : {nom}",
            compte_auteur=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # PATCH
    donnees = request.data
    if "nom" in donnees:
        nom = (donnees.get("nom") or "").strip()
        if not nom:
            return Response({"erreur": "Le nom ne peut pas être vide"}, status=status.HTTP_400_BAD_REQUEST)
        kit.nom = nom
    if "contenu" in donnees:
        contenu = (donnees.get("contenu") or "").strip()
        if not contenu:
            return Response({"erreur": "Le contenu ne peut pas être vide"}, status=status.HTTP_400_BAD_REQUEST)
        kit.contenu = contenu

    if "type_cible" in donnees:
        erreur = _appliquer_cible_kit(kit, donnees)
        if erreur:
            return erreur
        kit.save()
        if kit.type_cible == Kit.CIBLE_PARTICIPANTS:
            kit.participants_cibles.set(kit._participants_a_appliquer)
        else:
            kit.participants_cibles.clear()
    else:
        kit.save()

    return Response(_serialiser_kit(kit))


# =========================================================================
# DISTRIBUTION DES KITS (suivi par participant, une fois une cohorte
# terminée - voir aussi les certificats, gérés séparément dans
# vue_telecharger_certificats_lot)
# =========================================================================

@api_view(["GET"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_distribution_cohorte(request, id_cohorte):
    """
    GET /api/admin/cohorte/<id_cohorte>/distribution/
    Pour une cohorte (typiquement terminée), liste tous les kits qui la
    concernent - ciblés directement sur elle, sur sa formation, ou sur
    certains de ses participants - avec le statut de remise RÉEL par
    participant réellement concerné (une même personne peut apparaître
    plusieurs fois si plusieurs kits différents la concernent).
    """
    try:
        cohorte = Cohorte.objects.select_related("formation").get(pk=id_cohorte)
    except Cohorte.DoesNotExist:
        return Response({"erreur": "Cohorte introuvable"}, status=status.HTTP_404_NOT_FOUND)

    kits = Kit.objects.filter(
        Q(type_cible=Kit.CIBLE_COHORTE, cohorte=cohorte)
        | Q(type_cible=Kit.CIBLE_FORMATION, formation=cohorte.formation)
        | Q(type_cible=Kit.CIBLE_PARTICIPANTS, cohorte=cohorte)
    ).distinct().prefetch_related("participants_cibles")

    participants_cohorte = {
        i.participant_id: i.participant
        for i in cohorte.inscriptions.select_related("participant")
    }

    distributions_existantes = {
        (d.kit_id, d.participant_id): d
        for d in DistributionKit.objects.filter(
            kit__in=kits, participant_id__in=participants_cohorte.keys(),
        ).select_related("remis_par")
    }

    resultat = []
    for kit in kits:
        if kit.type_cible == Kit.CIBLE_PARTICIPANTS:
            cibles_ids = {p.id_participant for p in kit.participants_cibles.all()} & set(participants_cohorte.keys())
        else:
            cibles_ids = set(participants_cohorte.keys())

        for pid in sorted(cibles_ids, key=lambda i: (participants_cohorte[i].nom, participants_cohorte[i].prenom)):
            p = participants_cohorte[pid]
            d = distributions_existantes.get((kit.id_kit, pid))
            resultat.append({
                "id_kit": kit.id_kit,
                "nom_kit": kit.nom,
                "id_participant": pid,
                "nom": p.nom,
                "prenom": p.prenom,
                "distribue": d.distribue if d else False,
                "date_distribution": d.date_distribution if d else None,
                "remis_par": f"{d.remis_par.prenom} {d.remis_par.nom}" if d and d.remis_par else None,
            })

    return Response(resultat)


@api_view(["GET"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_distribution_kit(request, id_kit):
    """
    GET /api/admin/kit/<id_kit>/distribution/
    Liste TOUS les participants concernés par CE kit précis (résolus selon
    son type_cible - toute la formation, une cohorte, ou des participants
    précis), avec leur statut de remise réel. Contrairement à
    vue_distribution_cohorte (qui part d'une cohorte et liste ses kits),
    celle-ci part du kit et couvre toutes les cohortes concernées d'un
    coup - utile pour un kit ciblé sur une formation entière.
    """
    try:
        kit = Kit.objects.select_related("formation", "cohorte").prefetch_related("participants_cibles").get(pk=id_kit)
    except Kit.DoesNotExist:
        return Response({"erreur": "Kit introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if kit.type_cible == Kit.CIBLE_FORMATION:
        participants = Participant.objects.filter(inscriptions__cohorte__formation=kit.formation).distinct()
    elif kit.type_cible == Kit.CIBLE_COHORTE:
        participants = Participant.objects.filter(inscriptions__cohorte=kit.cohorte).distinct()
    else:
        participants = kit.participants_cibles.all()

    distributions = {
        d.participant_id: d
        for d in DistributionKit.objects.filter(kit=kit, participant__in=participants).select_related("remis_par")
    }

    resultat = []
    for p in participants.order_by("nom", "prenom"):
        d = distributions.get(p.id_participant)
        resultat.append({
            "id_participant": p.id_participant,
            "nom": p.nom,
            "prenom": p.prenom,
            "distribue": d.distribue if d else False,
            "date_distribution": d.date_distribution if d else None,
            "remis_par": f"{d.remis_par.prenom} {d.remis_par.nom}" if d and d.remis_par else None,
        })

    return Response(resultat)


@api_view(["POST"])
@permission_classes([APermission("GERER_FORMATIONS")])
def vue_basculer_distribution_kit(request, id_kit, id_participant):
    """
    POST /api/admin/kit/<id_kit>/participant/<id_participant>/distribuer/
    Bascule le statut de remise (Remis / Non remis) d'UN kit précis pour
    UN participant précis (aucun corps requis - simple bascule).
    """
    try:
        kit = Kit.objects.get(pk=id_kit)
        participant = Participant.objects.get(pk=id_participant)
    except Kit.DoesNotExist:
        return Response({"erreur": "Kit introuvable"}, status=status.HTTP_404_NOT_FOUND)
    except Participant.DoesNotExist:
        return Response({"erreur": "Participant introuvable"}, status=status.HTTP_404_NOT_FOUND)

    distribution, _ = DistributionKit.objects.get_or_create(kit=kit, participant=participant)
    distribution.distribue = not distribution.distribue
    distribution.date_distribution = timezone.now() if distribution.distribue else None
    distribution.remis_par = request.user if distribution.distribue else None
    distribution.save()

    return Response({
        "id_kit": kit.id_kit,
        "id_participant": participant.id_participant,
        "distribue": distribution.distribue,
        "date_distribution": distribution.date_distribution,
        "remis_par": f"{request.user.prenom} {request.user.nom}" if distribution.distribue else None,
    })


# =========================================================================
# SITE PUBLIC : endpoints en lecture seule (AllowAny), sans donnée
# personnelle, consommés par le site vitrine (projet Next.js séparé).
# =========================================================================

@api_view(["GET"])
@permission_classes([AllowAny])
def vue_publique_evenements(request):
    """
    GET /api/public/evenements/
    Événements ouverts au public (mode_inscription=OUVERT), à venir.
    Aucune donnée de participant/membre.
    """
    evenements = (
        Evenement.objects.filter(est_termine=False, mode_inscription="OUVERT")
        .order_by("id_evenement")
    )
    return Response([
        {
            "id_evenement": e.id_evenement,
            "titre": e.titre,
            "lieu": e.lieu,
            "type_evenement": e.type_evenement,
            "description": e.description,
            "seances": [
                {"date_seance": s.date_seance} for s in e.seances.order_by("numero_ordre")
            ],
        }
        for e in evenements
    ])


@api_view(["GET"])
@permission_classes([AllowAny])
def vue_publique_actions_sociales(request):
    """
    GET /api/public/actions-sociales/
    Résumé public des actions sociales (storytelling) - sans
    bénéficiaires ni aucune donnée personnelle.
    """
    actions = ActionSociale.objects.all().order_by("-date_debut")
    return Response([
        {
            "id_action": a.id_action,
            "titre": a.titre,
            "type_action": a.type_action,
            "type_action_libelle": a.get_type_action_display(),
            "description": a.description,
            "lieu": a.lieu,
            "date_debut": a.date_debut,
            "statut": a.statut,
        }
        for a in actions
    ])


@api_view(["GET"])
@permission_classes([AllowAny])
def vue_publique_impact(request):
    """
    GET /api/public/impact/
    Chiffres d'impact consolidés, sans aucune donnée personnelle -
    sous-ensemble sanitisé de vue_dashboard_stats.
    """
    return Response({
        "membres_actifs": Membre.objects.filter(statut_adhesion="ACTIF").count(),
        "beneficiaires_aides_total": Beneficiaire.objects.count(),
        "formations_terminees_total": Cohorte.objects.filter(statut="TERMINEE").count(),
        "kits_distribues_total": (
            DistributionKit.objects.filter(distribue=True).count()
            + InscriptionCohorte.objects.filter(kit_distribue=True).count()
        ),
        "actions_sociales_total": ActionSociale.objects.count(),
    })


def _serialiser_actualite(a):
    return {
        "id_actualite": a.id_actualite,
        "titre": a.titre,
        "contenu": a.contenu,
        "image": a.image.url if a.image else None,
        "date_publication": a.date_publication,
        "publie_par": f"{a.publie_par.prenom} {a.publie_par.nom}",
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def vue_publique_actualites(request):
    """GET /api/public/actualites/"""
    return Response([_serialiser_actualite(a) for a in Actualite.objects.select_related("publie_par").all()])


@api_view(["GET"])
@permission_classes([AllowAny])
def vue_publique_detail_actualite(request, id_actualite):
    """GET /api/public/actualites/<id>/"""
    try:
        actualite = Actualite.objects.select_related("publie_par").get(pk=id_actualite)
    except Actualite.DoesNotExist:
        return Response({"erreur": "Actualité introuvable"}, status=status.HTTP_404_NOT_FOUND)
    return Response(_serialiser_actualite(actualite))


@api_view(["POST"])
@permission_classes([APermission("GERER_COMMUNICATION")])
def vue_creer_actualite(request):
    """POST /api/admin/actualite/"""
    donnees = request.data
    for champ in ["titre", "contenu"]:
        if not donnees.get(champ):
            return Response({"erreur": f"Le champ '{champ}' est requis"}, status=status.HTTP_400_BAD_REQUEST)

    actualite = Actualite.objects.create(
        titre=donnees["titre"],
        contenu=donnees["contenu"],
        publie_par=request.user,
    )
    return Response(_serialiser_actualite(actualite), status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([APermission("GERER_COMMUNICATION")])
def vue_gerer_actualite(request, id_actualite):
    """PATCH/DELETE /api/admin/actualite/<id>/"""
    try:
        actualite = Actualite.objects.get(pk=id_actualite)
    except Actualite.DoesNotExist:
        return Response({"erreur": "Actualité introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        actualite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    donnees = request.data
    for champ in ["titre", "contenu"]:
        if champ in donnees:
            setattr(actualite, champ, donnees[champ])
    actualite.save()
    return Response(_serialiser_actualite(actualite))



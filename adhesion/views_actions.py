"""
adhesion/views_actions.py

Actions (étape 3) : l'association part de ses cibles et de leurs
besoins, puis organise les actions qui y répondent.

- Avant : cibles visées (à partir des besoins), partenaires, équipe de
  membres (affectés ou volontaires), tâches, budget.
- Pendant : cibles servies, présences de l'équipe, indicateurs.
- Après : bilan, besoins couverts, indicateurs de suivi.

Écriture : GERER_ACTIONS_SOCIALES. Lecture : aussi les gestionnaires des
cibles et des rapports. Les indicateurs « sensibles » (médicaux) ne sont
lus et écrits qu'avec DONNEES_MEDICALES.
"""

import datetime
import math

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .cibles import ids_zone_et_descendants
from .indicateurs import colonnes_depuis_saisie, est_vide, valeur_pour_affichage
from .models import (
    Action, ActionCible, ActionPartenaire, BesoinCible, Cible, Cohorte, DefinitionIndicateur, Evenement,
    JournalAudit, Membre, MembreEquipe, Notification, Partenaire, Seance, SeanceCohorte, TacheAction,
    TypeAction, ValeurIndicateur,
)
from .permissions import (
    APermission, APermissionUneParmi, EstAdmin, EstMembre, PermissionSelonMethode, compte_a_permission,
)

PERMISSION_ACTIONS = PermissionSelonMethode(
    lecture=APermissionUneParmi("GERER_ACTIONS_SOCIALES", "GERER_CIBLES", "VOIR_RAPPORTS"),
    ecriture=APermission("GERER_ACTIONS_SOCIALES"),
)
ECRITURE = APermission("GERER_ACTIONS_SOCIALES")
TAILLE_PAGE = 20


def _journaliser(request, type_action, description):
    JournalAudit.objects.create(type_action=type_action, description=description, compte_auteur=request.user)


def _notifier(comptes, titre, corps, lien=None, type_notification="INFO"):
    Notification.objects.bulk_create([
        Notification(compte_destinataire=compte, titre=titre[:200], corps=corps,
                     type_notification=type_notification, lien_action=lien)
        for compte in comptes if compte is not None
    ])


def _action_ou_404(id_action):
    try:
        return Action.objects.select_related("type_action", "zone", "responsable__compte").get(pk=id_action)
    except Action.DoesNotExist:
        return None


NON_TROUVEE = Response({"erreur": "Action introuvable"}, status=status.HTTP_404_NOT_FOUND)


def _verrouillee(action):
    if action.statut in ("TERMINEE", "ANNULEE"):
        libelle = "terminée" if action.statut == "TERMINEE" else "annulée"
        return Response(
            {"erreur": f"Cette action est {libelle} : seuls le bilan et les indicateurs de suivi restent modifiables."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


# =========================================================================
# SÉRIALISATION
# =========================================================================

class ActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Action
        fields = [
            "titre", "type_action", "description", "date_debut", "date_fin", "lieu", "zone", "responsable",
            "budget_prevu", "budget_realise", "appel_volontaires", "volontaires_souhaites", "bilan",
        ]

    def validate_titre(self, titre):
        titre = titre.strip()
        if not titre:
            raise serializers.ValidationError("Le titre est requis")
        return titre

    def validate_type_action(self, type_action):
        if self.instance is not None and type_action != self.instance.type_action and self.instance.valeurs.exists():
            raise serializers.ValidationError("Des indicateurs sont déjà saisis : le type ne peut plus changer")
        return type_action

    def validate(self, donnees):
        debut = donnees.get("date_debut", getattr(self.instance, "date_debut", None))
        fin = donnees.get("date_fin", getattr(self.instance, "date_fin", None))
        if debut and fin and fin < debut:
            raise serializers.ValidationError({"date_fin": "La date de fin précède la date de début"})
        return donnees


def _nom_membre(membre):
    return f"{membre.compte.prenom} {membre.compte.nom}" if membre else None


def _resume_action(action):
    return {
        "id_action": action.id_action,
        "titre": action.titre,
        "type_action": action.type_action_id,
        "type_action_libelle": action.type_action.libelle,
        "categorie": action.type_action.categorie,
        "statut": action.statut,
        "date_debut": action.date_debut,
        "date_fin": action.date_fin,
        "lieu": action.lieu,
        "zone_chemin": action.zone.chemin() if action.zone else None,
        "responsable": _nom_membre(action.responsable),
        "appel_volontaires": action.appel_volontaires,
        "nombre_cibles": getattr(action, "nombre_cibles", None),
        "nombre_cibles_servies": getattr(action, "nombre_cibles_servies", None),
        "nombre_equipe": getattr(action, "nombre_equipe", None),
        "taches_restantes": getattr(action, "taches_restantes", None),
    }


def _avec_compteurs(actions):
    return actions.select_related("type_action", "zone__parent__parent__parent", "responsable__compte").annotate(
        nombre_cibles=Count("cibles", distinct=True),
        nombre_cibles_servies=Count("cibles", filter=Q(cibles__statut="SERVIE"), distinct=True),
        nombre_equipe=Count("equipe", filter=Q(equipe__statut="CONFIRME"), distinct=True),
        taches_restantes=Count("taches", filter=Q(taches__faite=False), distinct=True),
    )


def _indicateurs_visibles(action, compte):
    voit_sensible = compte_a_permission(compte, "DONNEES_MEDICALES")
    return [
        i for i in action.type_action.indicateurs.filter(actif=True)
        if voit_sensible or not i.sensible
    ], voit_sensible


def _definition_json(i):
    return {
        "id_indicateur": i.id_indicateur, "code": i.code, "libelle": i.libelle, "type_valeur": i.type_valeur,
        "unite": i.unite, "choix": i.choix, "moment": i.moment, "niveau": i.niveau,
        "obligatoire": i.obligatoire, "sensible": i.sensible, "description": i.description,
    }


def _fiche(action, compte):
    action = _avec_compteurs(Action.objects.filter(pk=action.pk)).get()
    indicateurs, voit_sensible = _indicateurs_visibles(action, compte)
    ids_visibles = {i.id_indicateur for i in indicateurs}

    valeurs_par_cible, valeurs_action = {}, {}
    for v in action.valeurs.select_related("indicateur"):
        if v.indicateur_id not in ids_visibles:
            continue
        cible_valeurs = valeurs_par_cible.setdefault(v.action_cible_id, {}) if v.action_cible_id else valeurs_action
        cible_valeurs[str(v.indicateur_id)] = valeur_pour_affichage(v)

    return {
        **_resume_action(action),
        "description": action.description,
        "zone": action.zone_id,
        "id_responsable": action.responsable_id,
        "budget_prevu": action.budget_prevu,
        "budget_realise": action.budget_realise,
        "volontaires_souhaites": action.volontaires_souhaites,
        "bilan": action.bilan,
        "transitions_possibles": sorted(Action.TRANSITIONS[action.statut]),
        "types_cible": action.type_action.types_cible,
        "indicateurs": [_definition_json(i) for i in indicateurs],
        "indicateurs_masques": not voit_sensible and action.type_action.indicateurs.filter(sensible=True, actif=True).exists(),
        "valeurs_action": valeurs_action,
        "cibles": [
            {
                "id_action_cible": ac.id_action_cible,
                "id_cible": ac.cible_id,
                "nom_complet": ac.cible.nom_complet,
                "type_cible": ac.cible.type_cible,
                "telephone": ac.cible.telephone,
                "zone_chemin": ac.cible.zone.chemin() if ac.cible.zone else None,
                "besoin": ac.besoin.description if ac.besoin else None,
                "statut": ac.statut,
                "date_intervention": ac.date_intervention,
                "notes": ac.notes,
                "valeurs": valeurs_par_cible.get(ac.id_action_cible, {}),
            }
            for ac in action.cibles.select_related("cible__zone__parent__parent__parent", "besoin").order_by(
                "cible__nom", "cible__prenom"
            )
        ],
        "partenaires": [
            {
                "id_action_partenaire": ap.id_action_partenaire,
                "id_partenaire": ap.partenaire_id,
                "nom": ap.partenaire.nom,
                "type_partenaire": ap.partenaire.type_partenaire,
                "role": ap.role,
                "montant_apport": ap.montant_apport,
                "notes": ap.notes,
            }
            for ap in action.partenaires.select_related("partenaire")
        ],
        "equipe": [
            {
                "id_membre_equipe": e.id_membre_equipe,
                "id_membre": e.membre_id,
                "nom": _nom_membre(e.membre),
                "numero_adherent": e.membre.numero_adherent,
                "telephone": e.membre.compte.telephone,
                "role": e.role,
                "statut": e.statut,
                "volontaire": e.volontaire,
                "present": e.present,
            }
            for e in action.equipe.select_related("membre__compte").order_by("statut", "membre__compte__nom")
        ],
        "taches": [
            {
                "id_tache": t.id_tache,
                "titre": t.titre,
                "phase": t.phase,
                "responsable": _nom_membre(t.responsable),
                "id_responsable": t.responsable_id,
                "echeance": t.echeance,
                "faite": t.faite,
            }
            for t in action.taches.select_related("responsable__compte")
        ],
    }


# =========================================================================
# LISTE / CRÉATION / FICHE
# =========================================================================

def _ajouter_cibles(action, ids_cibles, besoins=()):
    """Ajoute des cibles (et celles des besoins choisis) ; ignore celles déjà présentes."""
    deja = set(action.cibles.values_list("cible_id", flat=True))
    ajoutees = 0
    for besoin in besoins:
        if besoin.cible_id not in deja:
            ActionCible.objects.create(action=action, cible_id=besoin.cible_id, besoin=besoin)
            deja.add(besoin.cible_id)
            ajoutees += 1
        if besoin.statut == "IDENTIFIE":
            besoin.statut = "PLANIFIE"
            besoin.save(update_fields=["statut"])
    for cible in Cible.objects.filter(pk__in=ids_cibles).exclude(pk__in=deja):
        ActionCible.objects.create(action=action, cible=cible)
        ajoutees += 1
    return ajoutees


@api_view(["GET", "POST"])
@permission_classes([PERMISSION_ACTIONS])
def vue_actions(request):
    """
    GET /api/admin/actions/?statut=&type=&zone=&q=&page=
    POST /api/admin/actions/ - {titre, type_action, date_debut, ..., cibles: [ids], besoins: [ids]}
    """
    if request.method == "POST":
        serializer = ActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        besoins = list(BesoinCible.objects.filter(pk__in=request.data.get("besoins") or []))
        with transaction.atomic():
            action = serializer.save(cree_par=request.user)
            _ajouter_cibles(action, request.data.get("cibles") or [], besoins)
        _journaliser(request, "CREATION_ACTION", f"Action créée : {action.titre}")
        return Response(_fiche(action, request.user), status=status.HTTP_201_CREATED)

    actions = Action.objects.all()
    params = request.query_params
    if params.get("statut"):
        actions = actions.filter(statut__in=params["statut"].split(","))
    if params.get("type"):
        actions = actions.filter(type_action_id=params["type"])
    if params.get("zone"):
        try:
            actions = actions.filter(zone_id__in=ids_zone_et_descendants(int(params["zone"])))
        except ValueError:
            return Response({"erreur": "zone invalide"}, status=status.HTTP_400_BAD_REQUEST)
    if params.get("q", "").strip():
        q = params["q"].strip()
        actions = actions.filter(Q(titre__icontains=q) | Q(lieu__icontains=q) | Q(description__icontains=q))

    total = actions.count()
    try:
        page = max(1, int(params.get("page", 1)))
    except ValueError:
        page = 1
    debut = (page - 1) * TAILLE_PAGE
    resultats = _avec_compteurs(actions.order_by("-date_debut", "-id_action"))[debut:debut + TAILLE_PAGE]
    return Response({
        "resultats": [_resume_action(a) for a in resultats],
        "total": total,
        "page": page,
        "pages": max(1, math.ceil(total / TAILLE_PAGE)),
    })


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([PERMISSION_ACTIONS])
def vue_action(request, id_action):
    """GET (fiche complète) / PATCH / DELETE (en préparation seulement) /api/admin/actions/<id>/"""
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE

    if request.method == "GET":
        return Response(_fiche(action, request.user))

    if request.method == "DELETE":
        if action.statut != "BROUILLON":
            return Response(
                {"erreur": "Seule une action en préparation peut être supprimée : annulez-la plutôt"},
                status=status.HTTP_409_CONFLICT,
            )
        titre = action.titre
        BesoinCible.objects.filter(interventions__action=action, statut="PLANIFIE").update(statut="IDENTIFIE")
        action.delete()
        _journaliser(request, "SUPPRESSION_ACTION", f"Action supprimée : {titre}")
        return Response(status=status.HTTP_204_NO_CONTENT)

    donnees = request.data
    if action.statut in ("TERMINEE", "ANNULEE") and set(donnees) - {"bilan", "budget_realise"}:
        return _verrouillee(action)
    serializer = ActionSerializer(action, data=donnees, partial=True)
    serializer.is_valid(raise_exception=True)
    action = serializer.save()
    _journaliser(request, "MODIFICATION_ACTION", f"Action modifiée : {action.titre}")
    return Response(_fiche(action, request.user))


@api_view(["POST"])
@permission_classes([ECRITURE])
def vue_changer_statut_action(request, id_action):
    """
    POST /api/admin/actions/<id>/statut/ - {statut}
    PLANIFIEE : l'équipe confirmée est convoquée (notification).
    TERMINEE : les besoins des cibles servies passent à « couvert ».
    """
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE
    nouveau = request.data.get("statut")
    if nouveau not in Action.TRANSITIONS[action.statut]:
        return Response(
            {"erreur": f"Passage impossible de « {action.get_statut_display()} » à « {nouveau} »"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        action.statut = nouveau
        action.save(update_fields=["statut"])
        if nouveau == "TERMINEE":
            BesoinCible.objects.filter(
                interventions__action=action, interventions__statut="SERVIE"
            ).update(statut="COUVERT")
        if nouveau == "ANNULEE":
            BesoinCible.objects.filter(interventions__action=action, statut="PLANIFIE").update(statut="IDENTIFIE")

    if nouveau == "PLANIFIEE":
        equipe = action.equipe.filter(statut="CONFIRME").select_related("membre__compte")
        _notifier(
            [e.membre.compte for e in equipe],
            f"Convocation : {action.titre}",
            f"Vous faites partie de l'équipe de « {action.titre} » le {action.date_debut:%d/%m/%Y}"
            + (f" ({action.lieu})" if action.lieu else "") + ".",
            lien="/membre/actions", type_notification="RAPPEL",
        )
    _journaliser(request, "STATUT_ACTION", f"{action.titre} : {action.get_statut_display()}")
    return Response(_fiche(action, request.user))


# =========================================================================
# CIBLES DE L'ACTION ET INDICATEURS
# =========================================================================

@api_view(["POST"])
@permission_classes([ECRITURE])
def vue_ajouter_cibles_action(request, id_action):
    """POST /api/admin/actions/<id>/cibles/ - {cibles: [ids], besoins: [ids]}"""
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE
    if (refus := _verrouillee(action)) is not None:
        return refus
    besoins = list(BesoinCible.objects.filter(pk__in=request.data.get("besoins") or []))
    with transaction.atomic():
        ajoutees = _ajouter_cibles(action, request.data.get("cibles") or [], besoins)
    return Response({"ajoutees": ajoutees, "fiche": _fiche(action, request.user)})


@api_view(["PATCH", "DELETE"])
@permission_classes([ECRITURE])
def vue_action_cible(request, id_action_cible):
    """PATCH {statut, date_intervention, notes} / DELETE /api/admin/action-cibles/<id>/"""
    try:
        action_cible = ActionCible.objects.select_related("action", "besoin").get(pk=id_action_cible)
    except ActionCible.DoesNotExist:
        return Response({"erreur": "Cible introuvable dans cette action"}, status=status.HTTP_404_NOT_FOUND)
    action = action_cible.action

    if request.method == "DELETE":
        if (refus := _verrouillee(action)) is not None:
            return refus
        if action_cible.besoin and action_cible.besoin.statut == "PLANIFIE":
            action_cible.besoin.statut = "IDENTIFIE"
            action_cible.besoin.save(update_fields=["statut"])
        action_cible.delete()
        return Response(_fiche(action, request.user))

    statut_cible = request.data.get("statut", action_cible.statut)
    if statut_cible not in dict(ActionCible.STATUT_CHOICES):
        return Response({"erreur": "Statut invalide"}, status=status.HTTP_400_BAD_REQUEST)
    action_cible.statut = statut_cible
    if "date_intervention" in request.data:
        action_cible.date_intervention = request.data["date_intervention"] or None
    elif statut_cible == "SERVIE" and action_cible.date_intervention is None:
        action_cible.date_intervention = timezone.localdate()
    if "notes" in request.data:
        action_cible.notes = str(request.data["notes"] or "")
    action_cible.save()
    return Response(_fiche(action, request.user))


@api_view(["POST"])
@permission_classes([ECRITURE])
def vue_statut_cibles_lot(request, id_action):
    """POST /api/admin/actions/<id>/cibles/statut/ - {ids: [id_action_cible], statut} : pointage rapide sur le terrain."""
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE
    statut_cible = request.data.get("statut")
    if statut_cible not in dict(ActionCible.STATUT_CHOICES):
        return Response({"erreur": "Statut invalide"}, status=status.HTTP_400_BAD_REQUEST)
    lignes = action.cibles.filter(pk__in=request.data.get("ids") or [])
    lignes.update(statut=statut_cible)
    if statut_cible == "SERVIE":
        lignes.filter(date_intervention__isnull=True).update(date_intervention=timezone.localdate())
    return Response(_fiche(action, request.user))


@api_view(["PUT"])
@permission_classes([ECRITURE])
def vue_enregistrer_valeurs(request, id_action):
    """
    PUT /api/admin/actions/<id>/valeurs/
    {valeurs: [{indicateur, action_cible (null = action entière), valeur}]}
    Une valeur vide efface la saisie. Tout ou rien : la première erreur
    annule l'ensemble et est renvoyée.
    """
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE
    indicateurs, voit_sensible = _indicateurs_visibles(action, request.user)
    par_id = {i.id_indicateur: i for i in indicateurs}
    ids_cibles = set(action.cibles.values_list("id_action_cible", flat=True))

    try:
        with transaction.atomic():
            for saisie in request.data.get("valeurs") or []:
                indicateur = par_id.get(saisie.get("indicateur"))
                if indicateur is None:
                    sensible = DefinitionIndicateur.objects.filter(
                        pk=saisie.get("indicateur"), type_action=action.type_action, sensible=True
                    ).exists()
                    raise ValueError(
                        "Indicateur médical : permission « Données médicales » requise" if sensible and not voit_sensible
                        else "Indicateur inconnu pour ce type d'action"
                    )
                if action.statut in ("TERMINEE", "ANNULEE") and indicateur.moment != "SUIVI":
                    raise ValueError("Action clôturée : seuls les indicateurs de suivi sont modifiables")
                id_action_cible = saisie.get("action_cible")
                if (indicateur.niveau == "CIBLE") != (id_action_cible is not None):
                    raise ValueError(
                        f"« {indicateur.libelle} » se saisit "
                        + ("pour chaque cible" if indicateur.niveau == "CIBLE" else "pour l'action entière")
                    )
                if id_action_cible is not None and id_action_cible not in ids_cibles:
                    raise ValueError("Cette cible ne fait pas partie de l'action")

                existante = ValeurIndicateur.objects.filter(
                    indicateur=indicateur, action=action, action_cible_id=id_action_cible
                )
                if est_vide(saisie.get("valeur")):
                    existante.delete()
                    continue
                colonnes = colonnes_depuis_saisie(indicateur, saisie.get("valeur"))
                ValeurIndicateur.objects.update_or_create(
                    indicateur=indicateur, action=action, action_cible_id=id_action_cible,
                    defaults={**colonnes, "saisi_par": request.user},
                )
    except ValueError as e:
        return Response({"erreur": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_fiche(action, request.user))


# =========================================================================
# PARTENAIRES, ÉQUIPE, TÂCHES
# =========================================================================

@api_view(["POST"])
@permission_classes([ECRITURE])
def vue_ajouter_partenaire_action(request, id_action):
    """POST /api/admin/actions/<id>/partenaires/ - {partenaire, role, montant_apport, notes}"""
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE
    try:
        partenaire = Partenaire.objects.get(pk=request.data.get("partenaire"))
    except (Partenaire.DoesNotExist, ValueError, TypeError):
        return Response({"erreur": "Partenaire introuvable"}, status=status.HTTP_404_NOT_FOUND)
    role = request.data.get("role", "AUTRE")
    if role not in dict(ActionPartenaire.ROLE_CHOICES):
        return Response({"erreur": "Rôle invalide"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        with transaction.atomic():
            ActionPartenaire.objects.create(
                action=action, partenaire=partenaire, role=role,
                montant_apport=request.data.get("montant_apport") or None,
                notes=str(request.data.get("notes") or "")[:255],
            )
    except IntegrityError:
        return Response({"erreur": "Ce partenaire participe déjà à l'action"}, status=status.HTTP_409_CONFLICT)
    return Response(_fiche(action, request.user), status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([ECRITURE])
def vue_retirer_partenaire_action(request, id_action_partenaire):
    """DELETE /api/admin/action-partenaires/<id>/"""
    try:
        lien = ActionPartenaire.objects.select_related("action").get(pk=id_action_partenaire)
    except ActionPartenaire.DoesNotExist:
        return Response({"erreur": "Partenaire introuvable dans cette action"}, status=status.HTTP_404_NOT_FOUND)
    action = lien.action
    lien.delete()
    return Response(_fiche(action, request.user))


@api_view(["POST"])
@permission_classes([ECRITURE])
def vue_affecter_membres(request, id_action):
    """
    POST /api/admin/actions/<id>/equipe/ - {membres: [ids], role}
    Affectation par le responsable : directement confirmée, le membre est notifié.
    """
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE
    role = str(request.data.get("role") or "").strip()[:100]
    deja = set(action.equipe.values_list("membre_id", flat=True))
    nouveaux = []
    for membre in Membre.objects.filter(pk__in=request.data.get("membres") or []).select_related("compte"):
        if membre.pk in deja:
            continue
        MembreEquipe.objects.create(action=action, membre=membre, role=role, statut="CONFIRME")
        nouveaux.append(membre.compte)
    _notifier(
        nouveaux, f"Mission : {action.titre}",
        f"Vous avez été affecté(e) à l'équipe de « {action.titre} »"
        + (f" comme {role}" if role else "") + f", le {action.date_debut:%d/%m/%Y}.",
        lien="/membre/actions",
    )
    return Response(_fiche(action, request.user))


@api_view(["PATCH", "DELETE"])
@permission_classes([ECRITURE])
def vue_membre_equipe(request, id_membre_equipe):
    """PATCH {role, statut, present} / DELETE /api/admin/equipe/<id>/"""
    try:
        ligne = MembreEquipe.objects.select_related("action", "membre__compte").get(pk=id_membre_equipe)
    except MembreEquipe.DoesNotExist:
        return Response({"erreur": "Membre introuvable dans l'équipe"}, status=status.HTTP_404_NOT_FOUND)
    action = ligne.action
    if request.method == "DELETE":
        ligne.delete()
        return Response(_fiche(action, request.user))

    ancien_statut = ligne.statut
    if "role" in request.data:
        ligne.role = str(request.data["role"] or "").strip()[:100]
    if "statut" in request.data:
        if request.data["statut"] not in dict(MembreEquipe.STATUT_CHOICES):
            return Response({"erreur": "Statut invalide"}, status=status.HTTP_400_BAD_REQUEST)
        ligne.statut = request.data["statut"]
    if "present" in request.data:
        ligne.present = request.data["present"]
    ligne.save()
    if ancien_statut == "PROPOSE" and ligne.statut in ("CONFIRME", "DECLINE"):
        retenu = ligne.statut == "CONFIRME"
        _notifier(
            [ligne.membre.compte],
            f"{'Candidature retenue' if retenu else 'Candidature non retenue'} : {action.titre}",
            (f"Merci ! Vous faites partie de l'équipe de « {action.titre} »."
             if retenu else f"L'équipe de « {action.titre} » est complète. Merci pour votre disponibilité."),
            lien="/membre/actions", type_notification="SUCCES" if retenu else "INFO",
        )
    return Response(_fiche(action, request.user))


@api_view(["POST"])
@permission_classes([ECRITURE])
def vue_ajouter_tache(request, id_action):
    """POST /api/admin/actions/<id>/taches/ - {titre, phase, responsable, echeance}"""
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE
    titre = str(request.data.get("titre") or "").strip()
    phase = request.data.get("phase", "AVANT")
    if not titre or phase not in dict(TacheAction.PHASE_CHOICES):
        return Response({"erreur": "Titre et phase valides requis"}, status=status.HTTP_400_BAD_REQUEST)
    TacheAction.objects.create(
        action=action, titre=titre[:200], phase=phase,
        responsable_id=request.data.get("responsable") or None,
        echeance=request.data.get("echeance") or None,
    )
    return Response(_fiche(action, request.user), status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([ECRITURE])
def vue_tache(request, id_tache):
    """PATCH {titre, phase, responsable, echeance, faite} / DELETE /api/admin/taches/<id>/"""
    try:
        tache = TacheAction.objects.select_related("action").get(pk=id_tache)
    except TacheAction.DoesNotExist:
        return Response({"erreur": "Tâche introuvable"}, status=status.HTTP_404_NOT_FOUND)
    action = tache.action
    if request.method == "DELETE":
        tache.delete()
        return Response(_fiche(action, request.user))
    for champ in ("titre", "phase", "echeance"):
        if champ in request.data:
            setattr(tache, champ, request.data[champ] or (None if champ == "echeance" else ""))
    if "responsable" in request.data:
        tache.responsable_id = request.data["responsable"] or None
    if "faite" in request.data:
        tache.faite = bool(request.data["faite"])
        tache.date_faite = timezone.now() if tache.faite else None
    if not tache.titre or tache.phase not in dict(TacheAction.PHASE_CHOICES):
        return Response({"erreur": "Titre et phase valides requis"}, status=status.HTTP_400_BAD_REQUEST)
    tache.save()
    return Response(_fiche(action, request.user))


# =========================================================================
# BESOINS DES CIBLES
# =========================================================================

def _besoin_json(b):
    return {
        "id_besoin": b.id_besoin,
        "id_cible": b.cible_id,
        "cible": b.cible.nom_complet,
        "type_cible": b.cible.type_cible,
        "zone_chemin": b.cible.zone.chemin() if b.cible.zone else None,
        "description": b.description,
        "type_action": b.type_action_id,
        "type_action_libelle": b.type_action.libelle if b.type_action else None,
        "priorite": b.priorite,
        "statut": b.statut,
        "date_identification": b.date_identification,
        "notes": b.notes,
    }


@api_view(["GET"])
@permission_classes([PERMISSION_ACTIONS])
def vue_besoins(request):
    """GET /api/admin/besoins/?statut=IDENTIFIE,PLANIFIE&type=&zone=&cible= - les besoins à couvrir."""
    besoins = BesoinCible.objects.select_related("cible__zone__parent__parent__parent", "type_action")
    params = request.query_params
    if params.get("statut"):
        besoins = besoins.filter(statut__in=params["statut"].split(","))
    if params.get("type"):
        besoins = besoins.filter(type_action_id=params["type"])
    if params.get("cible"):
        besoins = besoins.filter(cible_id=params["cible"])
    if params.get("zone"):
        try:
            besoins = besoins.filter(cible__zone_id__in=ids_zone_et_descendants(int(params["zone"])))
        except ValueError:
            return Response({"erreur": "zone invalide"}, status=status.HTTP_400_BAD_REQUEST)
    ordre_priorite = {"URGENTE": 0, "HAUTE": 1, "MOYENNE": 2, "BASSE": 3}
    resultats = sorted(besoins[:500], key=lambda b: (ordre_priorite[b.priorite], -b.pk))
    return Response([_besoin_json(b) for b in resultats])


@api_view(["POST"])
@permission_classes([APermissionUneParmi("GERER_CIBLES", "GERER_ACTIONS_SOCIALES")])
def vue_ajouter_besoin(request, id_cible):
    """POST /api/admin/cibles/<id>/besoins/ - {description, type_action, priorite, notes}"""
    try:
        cible = Cible.objects.get(pk=id_cible)
    except Cible.DoesNotExist:
        return Response({"erreur": "Cible introuvable"}, status=status.HTTP_404_NOT_FOUND)
    description = str(request.data.get("description") or "").strip()
    priorite = request.data.get("priorite", "MOYENNE")
    if not description:
        return Response({"erreur": "Décrivez le besoin"}, status=status.HTTP_400_BAD_REQUEST)
    if priorite not in dict(BesoinCible.PRIORITE_CHOICES):
        return Response({"erreur": "Priorité invalide"}, status=status.HTTP_400_BAD_REQUEST)
    type_action = None
    if request.data.get("type_action"):
        type_action = TypeAction.objects.filter(pk=request.data["type_action"]).first()
        if type_action is None:
            return Response({"erreur": "Type d'action inconnu"}, status=status.HTTP_400_BAD_REQUEST)
    besoin = BesoinCible.objects.create(
        cible=cible, description=description[:255], type_action=type_action, priorite=priorite,
        notes=str(request.data.get("notes") or ""), identifie_par=request.user,
    )
    _journaliser(request, "AJOUT_BESOIN", f"Besoin ajouté pour {cible.nom_complet} : {besoin.description}")
    return Response(_besoin_json(besoin), status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([APermissionUneParmi("GERER_CIBLES", "GERER_ACTIONS_SOCIALES")])
def vue_besoin(request, id_besoin):
    """PATCH {description, priorite, statut, type_action, notes} / DELETE /api/admin/besoins/<id>/"""
    try:
        besoin = BesoinCible.objects.select_related("cible__zone", "type_action").get(pk=id_besoin)
    except BesoinCible.DoesNotExist:
        return Response({"erreur": "Besoin introuvable"}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        if besoin.interventions.exists():
            return Response(
                {"erreur": "Une action répond déjà à ce besoin : marquez-le abandonné plutôt"},
                status=status.HTTP_409_CONFLICT,
            )
        besoin.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    for champ, choix in (("priorite", BesoinCible.PRIORITE_CHOICES), ("statut", BesoinCible.STATUT_CHOICES)):
        if champ in request.data:
            if request.data[champ] not in dict(choix):
                return Response({"erreur": f"{champ} invalide"}, status=status.HTTP_400_BAD_REQUEST)
            setattr(besoin, champ, request.data[champ])
    if "description" in request.data:
        if not str(request.data["description"]).strip():
            return Response({"erreur": "Décrivez le besoin"}, status=status.HTTP_400_BAD_REQUEST)
        besoin.description = str(request.data["description"]).strip()[:255]
    if "notes" in request.data:
        besoin.notes = str(request.data["notes"] or "")
    if "type_action" in request.data:
        besoin.type_action_id = request.data["type_action"] or None
    besoin.save()
    return Response(_besoin_json(besoin))


# =========================================================================
# CALENDRIER
# =========================================================================

def _periode(request):
    try:
        debut = datetime.date.fromisoformat(request.query_params["debut"])
        fin = datetime.date.fromisoformat(request.query_params["fin"])
    except (KeyError, ValueError):
        raise ValueError("Paramètres debut et fin requis (AAAA-MM-JJ)")
    if fin < debut or (fin - debut).days > 120:
        raise ValueError("Période invalide (120 jours au plus)")
    return debut, fin


@api_view(["GET"])
@permission_classes([EstAdmin])
def vue_calendrier(request):
    """
    GET /api/admin/calendrier/?debut=AAAA-MM-JJ&fin=AAAA-MM-JJ
    Actions, séances d'événements et séances de formation de la période :
    une vue d'ensemble de ce que fait l'association.
    """
    try:
        debut, fin = _periode(request)
    except ValueError as e:
        return Response({"erreur": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    elements = []
    actions = Action.objects.exclude(statut="ANNULEE").filter(
        date_debut__lte=fin
    ).filter(Q(date_fin__gte=debut) | Q(date_fin__isnull=True, date_debut__gte=debut)).select_related("type_action")
    for a in actions:
        elements.append({
            "nature": "ACTION", "id": a.id_action, "titre": a.titre, "sous_titre": a.type_action.libelle,
            "debut": a.date_debut, "fin": a.date_fin or a.date_debut, "statut": a.statut, "lieu": a.lieu,
        })
    for s in Seance.objects.filter(
        date_seance__date__range=(debut, fin), evenement__est_annule=False
    ).select_related("evenement"):
        jour = timezone.localtime(s.date_seance).date() if timezone.is_aware(s.date_seance) else s.date_seance.date()
        elements.append({
            "nature": "EVENEMENT", "id": s.evenement_id, "titre": s.evenement.titre,
            "sous_titre": s.evenement.get_type_evenement_display(), "debut": jour, "fin": jour,
            "statut": "TERMINE" if s.evenement.est_termine else "PREVU", "lieu": s.evenement.lieu,
        })
    for s in SeanceCohorte.objects.filter(date_seance__date__range=(debut, fin)).exclude(
        cohorte__statut="ANNULEE"
    ).select_related("cohorte__formation"):
        jour = timezone.localtime(s.date_seance).date() if timezone.is_aware(s.date_seance) else s.date_seance.date()
        elements.append({
            "nature": "FORMATION", "id": s.cohorte_id, "titre": s.cohorte.formation.titre,
            "sous_titre": f"Cohorte {s.cohorte.code_cohorte}", "debut": jour, "fin": jour,
            "statut": s.cohorte.statut, "lieu": s.cohorte.lieu,
        })
    elements.sort(key=lambda e: (e["debut"], e["titre"]))
    return Response(elements)


# =========================================================================
# ESPACE MEMBRE : appels à volontaires et missions
# =========================================================================

@api_view(["GET"])
@permission_classes([EstMembre])
def vue_mes_actions(request):
    """
    GET /api/membre/actions/
    - appels : actions ouvertes aux volontaires, auxquelles le membre ne participe pas encore ;
    - missions : actions où le membre est dans l'équipe (proposé, confirmé ou non retenu).
    """
    membre = request.user.membre
    missions = MembreEquipe.objects.filter(membre=membre).select_related("action__type_action", "action__zone")
    ids_missions = [m.action_id for m in missions]
    appels = (
        Action.objects.filter(appel_volontaires=True, statut__in=["BROUILLON", "PLANIFIEE"],
                              date_debut__gte=timezone.localdate())
        .exclude(pk__in=ids_missions).select_related("type_action", "zone")
        .annotate(nombre_equipe=Count("equipe", filter=Q(equipe__statut="CONFIRME")))
        .order_by("date_debut")
    )

    def action_json(a):
        return {
            "id_action": a.id_action, "titre": a.titre, "type_action_libelle": a.type_action.libelle,
            "description": a.description, "date_debut": a.date_debut, "date_fin": a.date_fin, "lieu": a.lieu,
            "zone_chemin": a.zone.chemin() if a.zone else None, "statut": a.statut,
        }

    return Response({
        "appels": [
            {**action_json(a), "volontaires_souhaites": a.volontaires_souhaites, "nombre_equipe": a.nombre_equipe}
            for a in appels
        ],
        "missions": [
            {**action_json(m.action), "id_membre_equipe": m.id_membre_equipe, "role": m.role,
             "statut_equipe": m.statut, "present": m.present}
            for m in sorted(missions, key=lambda m: m.action.date_debut, reverse=True)
        ],
    })


@api_view(["POST", "DELETE"])
@permission_classes([EstMembre])
def vue_volontariat(request, id_action):
    """
    POST /api/membre/actions/<id>/volontaire/ - se proposer (en attente de validation).
    DELETE - retirer sa proposition (ou se désister).
    """
    membre = request.user.membre
    action = _action_ou_404(id_action)
    if action is None:
        return NON_TROUVEE

    if request.method == "DELETE":
        ligne = MembreEquipe.objects.filter(action=action, membre=membre).first()
        if ligne is None:
            return Response({"erreur": "Vous ne faites pas partie de cette action"}, status=status.HTTP_404_NOT_FOUND)
        if ligne.statut == "PROPOSE":
            ligne.delete()
        else:
            ligne.statut = "DECLINE"
            ligne.save(update_fields=["statut"])
            if action.responsable and action.responsable != membre:
                _notifier(
                    [action.responsable.compte], f"Désistement : {action.titre}",
                    f"{_nom_membre(membre)} s'est désisté(e) de l'équipe.", lien=f"/admin/actions/{action.pk}",
                    type_notification="ALERTE",
                )
        return Response(status=status.HTTP_204_NO_CONTENT)

    if not action.appel_volontaires or action.statut not in ("BROUILLON", "PLANIFIEE"):
        return Response({"erreur": "Cette action n'accepte pas de volontaires"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        with transaction.atomic():
            MembreEquipe.objects.create(action=action, membre=membre, statut="PROPOSE", volontaire=True)
    except IntegrityError:
        return Response({"erreur": "Vous êtes déjà dans l'équipe de cette action"}, status=status.HTTP_409_CONFLICT)
    if action.responsable:
        _notifier(
            [action.responsable.compte], f"Nouveau volontaire : {action.titre}",
            f"{_nom_membre(membre)} se propose pour l'équipe.", lien=f"/admin/actions/{action.pk}",
        )
    return Response({"statut": "PROPOSE"}, status=status.HTTP_201_CREATED)

"""
adhesion/views_referentiels.py

API des référentiels du suivi des actions : zones, partenaires, types
d'action et leurs indicateurs.

Lecture : tout compte connecté pour les zones et types d'action
(nécessaires aux formulaires), admins pour les partenaires (coordonnées).
Écriture : permission GERER_REFERENTIELS.
"""

from django.db import IntegrityError
from django.db.models import Count, ProtectedError
from django.utils.text import slugify
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import (
    TYPES_CIBLE_CHOICES, DefinitionIndicateur, JournalAudit, Partenaire, TypeAction, Zone,
)
from .permissions import APermission, EstAdmin, EstAuthentifie, PermissionSelonMethode

# Partenaires : lecture par tout admin (choix dans les formulaires),
# écriture réservée à GERER_REFERENTIELS.
PERMISSION_PARTENAIRES = PermissionSelonMethode(lecture=EstAdmin, ecriture=APermission("GERER_REFERENTIELS"))

CODES_TYPES_CIBLE = {code for code, _ in TYPES_CIBLE_CHOICES}


def _journaliser(request, type_action, description):
    JournalAudit.objects.create(type_action=type_action, description=description, compte_auteur=request.user)


def _code_depuis_libelle(libelle, existe):
    """Slug unique dérivé du libellé ("Kits scolaires" -> "kits-scolaires", "kits-scolaires-2"...)."""
    base = slugify(libelle)[:50] or "element"
    code, n = base, 2
    while existe(code):
        code, n = f"{base}-{n}", n + 1
    return code


# =========================================================================
# ZONES
# =========================================================================

class ZoneSerializer(serializers.ModelSerializer):
    chemin = serializers.SerializerMethodField()
    nombre_sous_zones = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Zone
        fields = ["id_zone", "nom", "niveau", "parent", "actif", "chemin", "nombre_sous_zones"]
        # La cohérence parent/niveau et l'unicité sont vérifiées dans validate().
        validators = []

    def get_chemin(self, zone):
        return zone.chemin()

    def validate_nom(self, nom):
        nom = nom.strip()
        if not nom:
            raise serializers.ValidationError("Le nom est requis")
        return nom

    def validate(self, donnees):
        niveau = donnees.get("niveau", getattr(self.instance, "niveau", None))
        parent = donnees.get("parent", getattr(self.instance, "parent", None))
        nom = donnees.get("nom", getattr(self.instance, "nom", None))

        niveau_parent_attendu = Zone.NIVEAU_PARENT[niveau]
        if niveau_parent_attendu is None and parent is not None:
            raise serializers.ValidationError({"parent": "Une région n'a pas de zone parente"})
        if niveau_parent_attendu is not None:
            if parent is None:
                raise serializers.ValidationError({"parent": "Zone parente requise"})
            if parent.niveau != niveau_parent_attendu:
                libelle = dict(Zone.NIVEAU_CHOICES)[niveau_parent_attendu].lower()
                raise serializers.ValidationError({"parent": f"La zone parente doit être une {libelle}"})

        doublons = Zone.objects.filter(nom__iexact=nom, niveau=niveau, parent=parent)
        if self.instance is not None:
            doublons = doublons.exclude(pk=self.instance.pk)
        if doublons.exists():
            raise serializers.ValidationError({"nom": "Cette zone existe déjà à cet endroit"})
        return donnees


@api_view(["GET"])
@permission_classes([EstAuthentifie])
def vue_liste_zones(request):
    """
    GET /api/referentiels/zones/?parent=<id>|racine&niveau=...&q=...
    Sans filtre : les régions. `q` cherche par nom dans tous les niveaux.
    """
    zones = Zone.objects.select_related("parent__parent__parent").annotate(nombre_sous_zones=Count("sous_zones"))
    q = request.query_params.get("q", "").strip()
    parent = request.query_params.get("parent")
    niveau = request.query_params.get("niveau")

    if q:
        zones = zones.filter(nom__icontains=q)
    elif parent and parent != "racine":
        zones = zones.filter(parent_id=parent)
    elif not niveau:
        zones = zones.filter(parent__isnull=True)
    if niveau:
        zones = zones.filter(niveau=niveau)
    if request.query_params.get("inclure_inactives") != "1":
        zones = zones.filter(actif=True)
    return Response(ZoneSerializer(zones.order_by("nom")[:500], many=True).data)


@api_view(["POST"])
@permission_classes([APermission("GERER_REFERENTIELS")])
def vue_creer_zone(request):
    """POST /api/admin/zones/ - {nom, niveau, parent}"""
    serializer = ZoneSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    zone = serializer.save()
    _journaliser(request, "CREATION_ZONE", f"Zone créée : {zone.chemin()}")
    return Response(ZoneSerializer(zone).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([APermission("GERER_REFERENTIELS")])
def vue_gerer_zone(request, id_zone):
    """PATCH/DELETE /api/admin/zones/<id>/ - suppression refusée si la zone est utilisée."""
    try:
        zone = Zone.objects.get(pk=id_zone)
    except Zone.DoesNotExist:
        return Response({"erreur": "Zone introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        chemin = zone.chemin()
        try:
            zone.delete()
        except ProtectedError:
            return Response(
                {"erreur": "Cette zone contient des sous-zones : désactivez-la plutôt que de la supprimer"},
                status=status.HTTP_409_CONFLICT,
            )
        _journaliser(request, "SUPPRESSION_ZONE", f"Zone supprimée : {chemin}")
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = ZoneSerializer(zone, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    zone = serializer.save()
    _journaliser(request, "MODIFICATION_ZONE", f"Zone modifiée : {zone.chemin()}")
    return Response(ZoneSerializer(zone).data)


# =========================================================================
# PARTENAIRES
# =========================================================================

class PartenaireSerializer(serializers.ModelSerializer):
    zone_chemin = serializers.SerializerMethodField()

    class Meta:
        model = Partenaire
        fields = [
            "id_partenaire", "nom", "sigle", "type_partenaire", "domaines", "nom_contact", "telephone",
            "email", "adresse", "zone", "zone_chemin", "notes", "actif", "date_creation",
        ]
        read_only_fields = ["date_creation"]

    def get_zone_chemin(self, partenaire):
        return partenaire.zone.chemin() if partenaire.zone else None

    def validate_nom(self, nom):
        nom = nom.strip()
        doublons = Partenaire.objects.filter(nom__iexact=nom)
        if self.instance is not None:
            doublons = doublons.exclude(pk=self.instance.pk)
        if doublons.exists():
            raise serializers.ValidationError("Un partenaire porte déjà ce nom")
        return nom


@api_view(["GET", "POST"])
@permission_classes([PERMISSION_PARTENAIRES])
def vue_partenaires(request):
    """
    GET /api/admin/partenaires/?q=&type=&inclure_inactifs=1 - tout admin
    POST /api/admin/partenaires/ - GERER_REFERENTIELS
    """
    if request.method == "GET":
        partenaires = Partenaire.objects.select_related("zone__parent__parent__parent")
        q = request.query_params.get("q", "").strip()
        if q:
            partenaires = partenaires.filter(nom__icontains=q) | partenaires.filter(sigle__icontains=q)
        if request.query_params.get("type"):
            partenaires = partenaires.filter(type_partenaire=request.query_params["type"])
        if request.query_params.get("inclure_inactifs") != "1":
            partenaires = partenaires.filter(actif=True)
        return Response(PartenaireSerializer(partenaires.order_by("nom"), many=True).data)

    serializer = PartenaireSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    partenaire = serializer.save()
    _journaliser(request, "CREATION_PARTENAIRE", f"Partenaire créé : {partenaire.nom}")
    return Response(PartenaireSerializer(partenaire).data, status=status.HTTP_201_CREATED)



@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([PERMISSION_PARTENAIRES])
def vue_gerer_partenaire(request, id_partenaire):
    """GET (tout admin) / PATCH / DELETE (GERER_REFERENTIELS) /api/admin/partenaires/<id>/"""
    try:
        partenaire = Partenaire.objects.select_related("zone").get(pk=id_partenaire)
    except Partenaire.DoesNotExist:
        return Response({"erreur": "Partenaire introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(PartenaireSerializer(partenaire).data)

    if request.method == "DELETE":
        nom = partenaire.nom
        try:
            partenaire.delete()
        except ProtectedError:
            return Response(
                {"erreur": "Ce partenaire est lié à des actions : désactivez-le plutôt"},
                status=status.HTTP_409_CONFLICT,
            )
        _journaliser(request, "SUPPRESSION_PARTENAIRE", f"Partenaire supprimé : {nom}")
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = PartenaireSerializer(partenaire, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    partenaire = serializer.save()
    _journaliser(request, "MODIFICATION_PARTENAIRE", f"Partenaire modifié : {partenaire.nom}")
    return Response(PartenaireSerializer(partenaire).data)



# =========================================================================
# TYPES D'ACTION ET INDICATEURS
# =========================================================================

class IndicateurSerializer(serializers.ModelSerializer):
    code = serializers.SlugField(max_length=60, required=False)

    class Meta:
        model = DefinitionIndicateur
        fields = [
            "id_indicateur", "type_action", "code", "libelle", "description", "type_valeur", "unite",
            "choix", "moment", "niveau", "obligatoire", "sensible", "ordre", "actif",
        ]
        read_only_fields = ["type_action"]
        validators = []

    def validate_libelle(self, libelle):
        libelle = libelle.strip()
        if not libelle:
            raise serializers.ValidationError("Le libellé est requis")
        return libelle

    def validate(self, donnees):
        type_valeur = donnees.get("type_valeur", getattr(self.instance, "type_valeur", None))
        choix = donnees.get("choix", getattr(self.instance, "choix", []))

        if not isinstance(choix, list) or not all(isinstance(c, str) for c in choix):
            raise serializers.ValidationError({"choix": "Liste de libellés attendue"})
        choix = [c.strip() for c in choix if c.strip()]
        if type_valeur == "CHOIX":
            if len(choix) < 2:
                raise serializers.ValidationError({"choix": "Au moins deux choix sont requis"})
            if len({c.lower() for c in choix}) != len(choix):
                raise serializers.ValidationError({"choix": "Les choix doivent être distincts"})
        elif choix:
            raise serializers.ValidationError({"choix": "Les choix ne concernent que le type « Choix dans une liste »"})
        donnees["choix"] = choix

        type_action = self.context["type_action"]
        code = donnees.get("code")
        if code:
            doublons = DefinitionIndicateur.objects.filter(type_action=type_action, code=code)
            if self.instance is not None:
                doublons = doublons.exclude(pk=self.instance.pk)
            if doublons.exists():
                raise serializers.ValidationError({"code": "Ce code est déjà utilisé pour ce type d'action"})
        elif self.instance is None:
            donnees["code"] = _code_depuis_libelle(
                donnees["libelle"],
                lambda c: DefinitionIndicateur.objects.filter(type_action=type_action, code=c).exists(),
            )
        return donnees


class TypeActionSerializer(serializers.ModelSerializer):
    code = serializers.SlugField(max_length=40, required=False)
    indicateurs = serializers.SerializerMethodField()

    class Meta:
        model = TypeAction
        fields = [
            "id_type_action", "code", "libelle", "description", "categorie", "types_cible",
            "est_formation", "actif", "indicateurs",
        ]
        validators = []

    def get_indicateurs(self, type_action):
        indicateurs = type_action.indicateurs.all()
        if not self.context.get("inclure_inactifs"):
            indicateurs = [i for i in indicateurs if i.actif]
        return IndicateurSerializer(indicateurs, many=True, context={"type_action": type_action}).data

    def validate_libelle(self, libelle):
        libelle = libelle.strip()
        if not libelle:
            raise serializers.ValidationError("Le libellé est requis")
        return libelle

    def validate_types_cible(self, types_cible):
        if not isinstance(types_cible, list) or not types_cible:
            raise serializers.ValidationError("Choisissez au moins un type de cible")
        inconnus = set(types_cible) - CODES_TYPES_CIBLE
        if inconnus:
            raise serializers.ValidationError(f"Types de cible inconnus : {', '.join(sorted(map(str, inconnus)))}")
        return sorted(set(types_cible), key=[c for c, _ in TYPES_CIBLE_CHOICES].index)

    def validate(self, donnees):
        code = donnees.get("code")
        if code:
            doublons = TypeAction.objects.filter(code=code)
            if self.instance is not None:
                doublons = doublons.exclude(pk=self.instance.pk)
            if doublons.exists():
                raise serializers.ValidationError({"code": "Ce code est déjà utilisé"})
        elif self.instance is None:
            donnees["code"] = _code_depuis_libelle(
                donnees["libelle"], lambda c: TypeAction.objects.filter(code=c).exists()
            )
        return donnees


def _types_action(inclure_inactifs):
    types = TypeAction.objects.prefetch_related("indicateurs")
    return types if inclure_inactifs else types.filter(actif=True)


@api_view(["GET"])
@permission_classes([EstAuthentifie])
def vue_liste_types_action(request):
    """GET /api/referentiels/types-action/?inclure_inactifs=1 - avec leurs indicateurs."""
    inclure = request.query_params.get("inclure_inactifs") == "1"
    return Response(
        TypeActionSerializer(_types_action(inclure), many=True, context={"inclure_inactifs": inclure}).data
    )


@api_view(["POST"])
@permission_classes([APermission("GERER_REFERENTIELS")])
def vue_creer_type_action(request):
    """POST /api/admin/types-action/ - {libelle, categorie, types_cible, ...}"""
    serializer = TypeActionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    type_action = serializer.save()
    _journaliser(request, "CREATION_TYPE_ACTION", f"Type d'action créé : {type_action.libelle}")
    return Response(TypeActionSerializer(type_action).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([APermission("GERER_REFERENTIELS")])
def vue_gerer_type_action(request, id_type_action):
    """PATCH/DELETE /api/admin/types-action/<id>/"""
    try:
        type_action = TypeAction.objects.get(pk=id_type_action)
    except TypeAction.DoesNotExist:
        return Response({"erreur": "Type d'action introuvable"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        libelle = type_action.libelle
        try:
            type_action.delete()
        except ProtectedError:
            return Response(
                {"erreur": "Ce type d'action est utilisé : désactivez-le plutôt"},
                status=status.HTTP_409_CONFLICT,
            )
        _journaliser(request, "SUPPRESSION_TYPE_ACTION", f"Type d'action supprimé : {libelle}")
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = TypeActionSerializer(type_action, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    type_action = serializer.save()
    _journaliser(request, "MODIFICATION_TYPE_ACTION", f"Type d'action modifié : {type_action.libelle}")
    return Response(TypeActionSerializer(type_action, context={"inclure_inactifs": True}).data)


@api_view(["POST"])
@permission_classes([APermission("GERER_REFERENTIELS")])
def vue_creer_indicateur(request, id_type_action):
    """POST /api/admin/types-action/<id>/indicateurs/"""
    try:
        type_action = TypeAction.objects.get(pk=id_type_action)
    except TypeAction.DoesNotExist:
        return Response({"erreur": "Type d'action introuvable"}, status=status.HTTP_404_NOT_FOUND)

    serializer = IndicateurSerializer(data=request.data, context={"type_action": type_action})
    serializer.is_valid(raise_exception=True)
    if "ordre" not in request.data:
        dernier = type_action.indicateurs.order_by("-ordre").values_list("ordre", flat=True).first() or 0
        serializer.validated_data["ordre"] = dernier + 1
    try:
        indicateur = serializer.save(type_action=type_action)
    except IntegrityError:
        return Response({"erreur": "Ce code est déjà utilisé pour ce type d'action"}, status=status.HTTP_409_CONFLICT)
    _journaliser(
        request, "CREATION_INDICATEUR", f"Indicateur créé : {indicateur.libelle} ({type_action.libelle})"
    )
    return Response(
        IndicateurSerializer(indicateur, context={"type_action": type_action}).data, status=status.HTTP_201_CREATED
    )


@api_view(["PATCH", "DELETE"])
@permission_classes([APermission("GERER_REFERENTIELS")])
def vue_gerer_indicateur(request, id_indicateur):
    """PATCH/DELETE /api/admin/indicateurs/<id>/"""
    try:
        indicateur = DefinitionIndicateur.objects.select_related("type_action").get(pk=id_indicateur)
    except DefinitionIndicateur.DoesNotExist:
        return Response({"erreur": "Indicateur introuvable"}, status=status.HTTP_404_NOT_FOUND)
    contexte = {"type_action": indicateur.type_action}

    if request.method == "DELETE":
        libelle = indicateur.libelle
        try:
            indicateur.delete()
        except ProtectedError:
            return Response(
                {"erreur": "Cet indicateur a déjà des valeurs saisies : désactivez-le plutôt"},
                status=status.HTTP_409_CONFLICT,
            )
        _journaliser(request, "SUPPRESSION_INDICATEUR", f"Indicateur supprimé : {libelle}")
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = IndicateurSerializer(indicateur, data=request.data, partial=True, context=contexte)
    serializer.is_valid(raise_exception=True)
    indicateur = serializer.save()
    _journaliser(request, "MODIFICATION_INDICATEUR", f"Indicateur modifié : {indicateur.libelle}")
    return Response(IndicateurSerializer(indicateur, context=contexte).data)

"""
adhesion/cibles.py

Règles métier des cibles : normalisation, détection des doublons,
fusion, reprise des bénéficiaires des anciennes actions sociales.

Les fonctions de normalisation n'importent aucun modèle : elles sont
aussi utilisées par les migrations.
"""

import re
import unicodedata

from django.db import transaction

PERSONNE = "PERSONNE"
MAX_DOUBLONS = 10


# =========================================================================
# NORMALISATION
# =========================================================================

def normaliser_telephone(telephone: str | None) -> str:
    """
    Chiffres seuls, sans l'indicatif du Sénégal : "+221 77 123 45 67",
    "00221771234567" et "77 123 45 67" donnent tous "771234567".
    """
    chiffres = re.sub(r"\D", "", telephone or "")
    if chiffres.startswith("00221"):
        chiffres = chiffres[5:]
    elif chiffres.startswith("221") and len(chiffres) == 12:
        chiffres = chiffres[3:]
    return chiffres


def normaliser_texte(texte: str | None) -> str:
    """Minuscules, sans accents ni ponctuation, espaces réduits : "  N'Diaye-Sow " -> "n diaye sow"."""
    sans_accents = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans_accents.lower()).split())


# =========================================================================
# ZONES
# =========================================================================

def ids_zone_et_descendants(id_zone: int) -> list[int]:
    """La zone et toutes ses sous-zones (4 niveaux au plus : une requête par niveau)."""
    from .models import Zone

    ids, niveau = [id_zone], [id_zone]
    while niveau:
        niveau = list(Zone.objects.filter(parent_id__in=niveau).values_list("id_zone", flat=True))
        ids.extend(niveau)
    return ids


# =========================================================================
# DOUBLONS
# =========================================================================

def doublons_potentiels(donnees: dict, exclure_id: int | None = None) -> list[tuple]:
    """
    Cibles existantes qui pourraient être la même que `donnees` (champs
    d'une cible, avant création ou modification). Retourne une liste de
    (cible, [raisons]) ; vide si aucun doute.

    Personne : même téléphone, même pièce d'identité, ou mêmes nom et
    prénom (avec la même date de naissance si elle est connue des deux
    côtés). Collectif : même type et même nom dans la même zone, ou même
    téléphone.
    """
    from .models import Cible

    type_cible = donnees.get("type_cible")
    telephone = normaliser_telephone(donnees.get("telephone"))
    nom = normaliser_texte(donnees.get("nom"))
    candidates = Cible.objects.filter(type_cible=type_cible).select_related("zone")
    if exclure_id is not None:
        candidates = candidates.exclude(pk=exclure_id)

    raisons: dict[int, tuple] = {}

    def ajouter(cible, raison):
        raisons.setdefault(cible.pk, (cible, []))[1].append(raison)

    if len(telephone) >= 7:
        for cible in candidates.filter(telephone_normalise=telephone)[:MAX_DOUBLONS]:
            ajouter(cible, "Même téléphone")

    if type_cible == PERSONNE:
        piece = normaliser_texte(donnees.get("numero_identification"))
        if piece:
            for cible in candidates.exclude(numero_identification=""):
                if normaliser_texte(cible.numero_identification) == piece:
                    ajouter(cible, "Même pièce d'identité")

        prenom = normaliser_texte(donnees.get("prenom"))
        date_naissance = donnees.get("date_naissance")
        if nom and prenom:
            # Comparaison sur les formes normalisées : accents, tirets et
            # casse ne doivent pas cacher un doublon.
            for cible in candidates.filter(nom_normalise=nom, prenom_normalise=prenom)[:MAX_DOUBLONS]:
                if date_naissance and cible.date_naissance and str(cible.date_naissance) != str(date_naissance):
                    continue
                meme_date = bool(date_naissance and cible.date_naissance)
                ajouter(cible, "Mêmes nom, prénom et date de naissance" if meme_date else "Mêmes nom et prénom")
    elif nom:
        id_zone = donnees.get("zone")
        id_zone = getattr(id_zone, "pk", id_zone)
        for cible in candidates.filter(nom_normalise=nom)[:50]:
            if id_zone and cible.zone_id and cible.zone_id != id_zone:
                continue
            ajouter(cible, "Même nom" + (" dans la même zone" if id_zone and cible.zone_id else ""))

    return list(raisons.values())[:MAX_DOUBLONS]


# =========================================================================
# FUSION
# =========================================================================

CHAMPS_FUSIONNABLES = [
    "prenom", "sexe", "date_naissance", "numero_identification", "sous_type", "responsable", "effectif",
    "telephone", "email", "zone", "adresse", "latitude", "longitude",
]


@transaction.atomic
def fusionner(principale, doublon):
    """
    Rattache tout ce qui concerne `doublon` à `principale`, puis supprime
    `doublon`. Les champs vides de la cible principale sont complétés,
    jamais écrasés ; les notes sont mises bout à bout.

    À compléter à chaque nouvelle table qui référence une cible (actions,
    suivis... dans les étapes suivantes).
    """
    from .models import Appartenance

    if principale.pk == doublon.pk:
        raise ValueError("Une cible ne peut pas être fusionnée avec elle-même")
    if principale.type_cible != doublon.type_cible:
        raise ValueError("Seules deux cibles du même type peuvent être fusionnées")

    for champ in CHAMPS_FUSIONNABLES:
        if getattr(principale, champ) in (None, "") and getattr(doublon, champ) not in (None, ""):
            setattr(principale, champ, getattr(doublon, champ))
    if doublon.notes:
        principale.notes = "\n".join(filter(None, [principale.notes, doublon.notes]))

    # Liens un-à-un : libérés sur le doublon avant d'être repris.
    for lien in ("membre", "beneficiaire_origine"):
        if getattr(principale, lien) is None and getattr(doublon, lien) is not None:
            valeur = getattr(doublon, lien)
            setattr(doublon, lien, None)
            doublon.save(update_fields=[lien])
            setattr(principale, lien, valeur)
    principale.save()

    for champ_cote, champ_autre in (("personne", "collectif"), ("collectif", "personne")):
        for appartenance in Appartenance.objects.filter(**{champ_cote: doublon}):
            deja = Appartenance.objects.filter(
                **{champ_cote: principale, champ_autre: getattr(appartenance, champ_autre)}
            ).exists()
            if deja or getattr(appartenance, champ_autre) == principale:
                appartenance.delete()
            else:
                setattr(appartenance, champ_cote, principale)
                appartenance.save(update_fields=[champ_cote])

    doublon.delete()
    return principale


# =========================================================================
# REPRISE DES BÉNÉFICIAIRES (anciennes actions sociales)
# =========================================================================

def cible_pour_beneficiaire(beneficiaire):
    """
    Cible PERSONNE correspondant à un bénéficiaire des anciennes actions
    sociales : celle déjà liée, sinon une cible existante de même
    téléphone encore libre, sinon une nouvelle.
    """
    from .models import Cible

    existante = Cible.objects.filter(beneficiaire_origine=beneficiaire).first()
    if existante:
        return existante

    telephone = normaliser_telephone(beneficiaire.telephone)
    if len(telephone) >= 7:
        libre = Cible.objects.filter(
            type_cible=PERSONNE, telephone_normalise=telephone, beneficiaire_origine__isnull=True
        ).first()
        if libre:
            libre.beneficiaire_origine = beneficiaire
            libre.save(update_fields=["beneficiaire_origine"])
            return libre

    return Cible.objects.create(
        type_cible=PERSONNE,
        nom=beneficiaire.nom,
        prenom=beneficiaire.prenom,
        sexe=beneficiaire.sexe or "",
        date_naissance=beneficiaire.date_naissance,
        numero_identification=beneficiaire.numero_identification or "",
        telephone=beneficiaire.telephone or "",
        adresse=(beneficiaire.adresse or "")[:255],
        membre=beneficiaire.membre if beneficiaire.membre and not hasattr(beneficiaire.membre, "cible") else None,
        beneficiaire_origine=beneficiaire,
    )

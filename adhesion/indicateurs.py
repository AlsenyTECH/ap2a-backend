"""
adhesion/indicateurs.py

Conversion des valeurs d'indicateurs entre la saisie (JSON) et le
stockage typé de ValeurIndicateur, selon le type de l'indicateur.
"""

import datetime
from decimal import Decimal, InvalidOperation

COLONNES = ("valeur_nombre", "valeur_texte", "valeur_booleen", "valeur_date")
NUMERIQUES = {"ENTIER", "DECIMAL", "MONTANT", "POURCENTAGE"}


def est_vide(brut) -> bool:
    return brut is None or (isinstance(brut, str) and not brut.strip())


def colonnes_depuis_saisie(indicateur, brut) -> dict:
    """
    Valeurs des colonnes de ValeurIndicateur pour une saisie `brut`.
    Lève ValueError avec un message lisible si la saisie ne convient pas
    au type de l'indicateur. (Une saisie vide est traitée par l'appelant :
    elle efface la valeur.)
    """
    colonnes = {"valeur_nombre": None, "valeur_texte": "", "valeur_booleen": None, "valeur_date": None}
    type_valeur = indicateur.type_valeur

    if type_valeur in NUMERIQUES:
        try:
            nombre = Decimal(str(brut).replace(" ", "").replace(",", "."))
        except (InvalidOperation, ValueError):
            raise ValueError(f"« {indicateur.libelle} » attend un nombre")
        if not nombre.is_finite():
            raise ValueError(f"« {indicateur.libelle} » attend un nombre")
        if type_valeur in ("ENTIER", "MONTANT") and nombre != nombre.to_integral_value():
            raise ValueError(f"« {indicateur.libelle} » attend un nombre entier")
        if nombre < 0:
            raise ValueError(f"« {indicateur.libelle} » ne peut pas être négatif")
        if type_valeur == "POURCENTAGE" and nombre > 100:
            raise ValueError(f"« {indicateur.libelle} » est un pourcentage (0 à 100)")
        colonnes["valeur_nombre"] = nombre
    elif type_valeur == "BOOLEEN":
        if isinstance(brut, bool):
            colonnes["valeur_booleen"] = brut
        elif str(brut).strip().lower() in ("oui", "true", "1", "vrai"):
            colonnes["valeur_booleen"] = True
        elif str(brut).strip().lower() in ("non", "false", "0", "faux"):
            colonnes["valeur_booleen"] = False
        else:
            raise ValueError(f"« {indicateur.libelle} » attend oui ou non")
    elif type_valeur == "CHOIX":
        if brut not in indicateur.choix:
            raise ValueError(f"« {indicateur.libelle} » : choisissez parmi {', '.join(indicateur.choix)}")
        colonnes["valeur_texte"] = brut
    elif type_valeur == "DATE":
        try:
            colonnes["valeur_date"] = datetime.date.fromisoformat(str(brut))
        except ValueError:
            raise ValueError(f"« {indicateur.libelle} » attend une date (AAAA-MM-JJ)")
    else:
        colonnes["valeur_texte"] = str(brut).strip()[:5000]
    return colonnes


def valeur_pour_affichage(valeur):
    """La valeur stockée, sous sa forme JSON naturelle."""
    type_valeur = valeur.indicateur.type_valeur
    if type_valeur in NUMERIQUES:
        if valeur.valeur_nombre is None:
            return None
        nombre = valeur.valeur_nombre
        return int(nombre) if nombre == nombre.to_integral_value() else float(nombre)
    if type_valeur == "BOOLEEN":
        return valeur.valeur_booleen
    if type_valeur == "DATE":
        return valeur.valeur_date.isoformat() if valeur.valeur_date else None
    return valeur.valeur_texte

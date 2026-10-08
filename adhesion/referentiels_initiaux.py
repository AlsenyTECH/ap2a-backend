"""
adhesion/referentiels_initiaux.py

Données de départ des référentiels, chargées par migration. Données
pures (aucun import de modèle) pour rester utilisables depuis une
migration.

- Découpage administratif du Sénégal : 14 régions, 46 départements
  (source : portail officiel vie-publique.sn). Les communes et
  quartiers sont ajoutés par l'association selon ses zones d'action.

- Types d'action et indicateurs : modèles de départ, modifiables par
  un admin. Inspirés des pratiques courantes de suivi-évaluation :
  données désagrégées par sexe et par âge (ventilation demandée par la
  plupart des bailleurs), situation de référence / intervention / suivi
  pour mesurer un changement, et pour l'urgence les rubriques des
  standards Sphere (abris, articles non alimentaires, vivres).
"""

REGIONS_DEPARTEMENTS = {
    "Dakar": ["Dakar", "Guédiawaye", "Keur Massar", "Pikine", "Rufisque"],
    "Diourbel": ["Bambey", "Diourbel", "Mbacké"],
    "Fatick": ["Fatick", "Foundiougne", "Gossas"],
    "Kaffrine": ["Birkilane", "Kaffrine", "Koungheul", "Malem Hodar"],
    "Kaolack": ["Guinguinéo", "Kaolack", "Nioro du Rip"],
    "Kédougou": ["Kédougou", "Salémata", "Saraya"],
    "Kolda": ["Kolda", "Médina Yoro Foulah", "Vélingara"],
    "Louga": ["Kébémer", "Linguère", "Louga"],
    "Matam": ["Kanel", "Matam", "Ranérou Ferlo"],
    "Saint-Louis": ["Dagana", "Podor", "Saint-Louis"],
    "Sédhiou": ["Bounkiling", "Goudomp", "Sédhiou"],
    "Tambacounda": ["Bakel", "Goudiry", "Koumpentoum", "Tambacounda"],
    "Thiès": ["Mbour", "Thiès", "Tivaouane"],
    "Ziguinchor": ["Bignona", "Oussouye", "Ziguinchor"],
}

# Communes d'arrondissement du département de Dakar (19), par
# arrondissement : Almadies, Grand Dakar, Parcelles Assainies, Plateau/Gorée.
COMMUNES = {
    ("Dakar", "Dakar"): [
        "Ngor", "Ouakam", "Yoff", "Mermoz-Sacré-Cœur",
        "Grand Dakar", "Biscuiterie", "HLM", "Hann Bel-Air", "Sicap-Liberté", "Dieuppeul-Derklé",
        "Parcelles Assainies", "Cambérène", "Grand Yoff", "Patte d'Oie",
        "Plateau", "Gorée", "Médina", "Fann-Point E-Amitié", "Gueule Tapée-Fass-Colobane",
    ],
}

# Quartiers préchargés, par (région, département, commune). Les Parcelles
# Assainies sont découpées en unités numérotées : les Unités 1 à 26
# forment le site d'origine (1974). Le plan de développement communal
# 2021-2025 ne compte que les Unités 7 à 26 dans la commune ; une unité se
# rattache à une autre commune depuis Référentiels > Zones si besoin.
QUARTIERS = {
    ("Dakar", "Dakar", "Parcelles Assainies"): [f"Unité {numero}" for numero in range(1, 27)],
}

ETAT_BATIMENT = ["Bon", "Moyen", "Dégradé", "Inutilisable"]


def _ind(code, libelle, type_valeur, moment="INTERVENTION", niveau="CIBLE", unite="", choix=None,
         obligatoire=False, sensible=False, description=""):
    return {
        "code": code, "libelle": libelle, "type_valeur": type_valeur, "moment": moment,
        "niveau": niveau, "unite": unite, "choix": choix or [], "obligatoire": obligatoire,
        "sensible": sensible, "description": description,
    }


TYPES_ACTION = [
    {
        "code": "formation",
        "libelle": "Formation professionnelle",
        "categorie": "FORMATION",
        "types_cible": ["PERSONNE", "GROUPE", "ASC"],
        "est_formation": True,
        "description": "Formation avec un organisme partenaire, suivie de l'insertion des apprenants.",
        "indicateurs": [
            _ind("niveau_initial", "Niveau initial dans le domaine", "CHOIX", "REFERENCE",
                 choix=["Aucun", "Débutant", "Intermédiaire", "Confirmé"]),
            _ind("activite_avant", "Exerçait une activité rémunérée avant la formation", "BOOLEEN", "REFERENCE"),
            _ind("assiduite", "Taux de présence aux séances", "POURCENTAGE", unite="%"),
            _ind("certifie", "Certification obtenue", "BOOLEEN", obligatoire=True),
            _ind("kit_recu", "Kit d'installation reçu", "BOOLEEN"),
            _ind("situation_insertion", "Situation d'insertion", "CHOIX", "SUIVI", obligatoire=True,
                 choix=["Sans activité", "En recherche", "Emploi salarié", "Auto-emploi / activité créée",
                        "Poursuite de formation"]),
            _ind("revenu_mensuel", "Revenu mensuel tiré de l'activité", "MONTANT", "SUIVI", unite="FCFA"),
            _ind("emplois_crees", "Emplois créés (en plus du bénéficiaire)", "ENTIER", "SUIVI"),
        ],
    },
    {
        "code": "fournitures-scolaires",
        "libelle": "Fournitures / kits scolaires",
        "categorie": "EDUCATION",
        "types_cible": ["PERSONNE", "ETABLISSEMENT"],
        "description": "Remise de kits scolaires à des élèves ou à des écoles.",
        "indicateurs": [
            _ind("critere_selection", "Critère de sélection", "CHOIX", "REFERENCE",
                 choix=["Vulnérabilité sociale", "Orphelin", "Mérite scolaire", "Handicap", "Autre"]),
            _ind("kits_distribues", "Kits distribués", "ENTIER", unite="kits", obligatoire=True),
            _ind("eleves_filles", "Élèves bénéficiaires - filles", "ENTIER", unite="élèves"),
            _ind("eleves_garcons", "Élèves bénéficiaires - garçons", "ENTIER", unite="élèves"),
            _ind("valeur_kits", "Valeur des kits remis", "MONTANT", unite="FCFA"),
            _ind("date_remise", "Date de remise", "DATE"),
            _ind("toujours_scolarise", "Toujours scolarisé(e)", "BOOLEEN", "SUIVI"),
            _ind("passage_classe", "Passage en classe supérieure", "BOOLEEN", "SUIVI"),
            _ind("abandons", "Abandons constatés parmi les bénéficiaires", "ENTIER", "SUIVI", unite="élèves"),
        ],
    },
    {
        "code": "rehabilitation-ecole",
        "libelle": "Réhabilitation d'établissement scolaire",
        "categorie": "INFRASTRUCTURE",
        "types_cible": ["ETABLISSEMENT"],
        "description": "Rénovation ou équipement d'une école (salles, latrines, eau, mobilier).",
        "indicateurs": [
            _ind("etat_avant", "État général avant travaux", "CHOIX", "REFERENCE", choix=ETAT_BATIMENT,
                 obligatoire=True),
            _ind("effectif_eleves", "Effectif des élèves", "ENTIER", "REFERENCE", unite="élèves"),
            _ind("salles_rehabilitees", "Salles de classe réhabilitées", "ENTIER", unite="salles"),
            _ind("salles_construites", "Salles de classe construites", "ENTIER", unite="salles"),
            _ind("latrines", "Blocs de latrines construits ou réhabilités", "ENTIER", unite="blocs"),
            _ind("point_eau", "Point d'eau installé", "BOOLEEN"),
            _ind("tables_bancs", "Tables-bancs fournis", "ENTIER", unite="tables-bancs"),
            _ind("cout_travaux", "Coût des travaux", "MONTANT", unite="FCFA"),
            _ind("etat_apres", "État général après travaux", "CHOIX", "SUIVI", choix=ETAT_BATIMENT,
                 obligatoire=True),
            _ind("salles_fonctionnelles", "Salles fonctionnelles", "ENTIER", "SUIVI", unite="salles"),
            _ind("effectif_suivi", "Effectif des élèves au suivi", "ENTIER", "SUIVI", unite="élèves"),
        ],
    },
    {
        "code": "appui-sinistres",
        "libelle": "Appui aux sinistrés (inondations, pluies)",
        "categorie": "URGENCE",
        "types_cible": ["ZONE_SINISTREE", "PERSONNE", "GROUPE"],
        "description": "Assistance aux ménages et localités touchés par les pluies et inondations.",
        "indicateurs": [
            _ind("menages_affectes", "Ménages affectés", "ENTIER", "REFERENCE", unite="ménages", obligatoire=True),
            _ind("personnes_affectees", "Personnes affectées", "ENTIER", "REFERENCE", unite="personnes"),
            _ind("habitations_endommagees", "Habitations endommagées", "ENTIER", "REFERENCE", unite="habitations"),
            _ind("habitations_detruites", "Habitations détruites", "ENTIER", "REFERENCE", unite="habitations"),
            _ind("menages_assistes", "Ménages assistés", "ENTIER", unite="ménages", obligatoire=True),
            _ind("personnes_assistees", "Personnes assistées", "ENTIER", unite="personnes"),
            _ind("enfants_moins_5_ans", "dont enfants de moins de 5 ans", "ENTIER", unite="enfants"),
            _ind("vivres_kg", "Vivres distribués", "DECIMAL", unite="kg"),
            _ind("kits_non_alimentaires", "Kits non alimentaires (nattes, moustiquaires, ustensiles...)",
                 "ENTIER", unite="kits"),
            _ind("kits_hygiene", "Kits d'hygiène (savon, eau de Javel...)", "ENTIER", unite="kits"),
            _ind("motopompes", "Motopompes mises à disposition", "ENTIER", unite="motopompes"),
            _ind("valeur_assistance", "Valeur de l'assistance", "MONTANT", unite="FCFA"),
            _ind("menages_reloges", "Ménages relogés ou rentrés chez eux", "ENTIER", "SUIVI", unite="ménages"),
            _ind("eau_evacuee", "Eaux stagnantes évacuées", "BOOLEEN", "SUIVI"),
        ],
    },
    {
        "code": "campagne-medicale",
        "libelle": "Visite médicale et prise en charge",
        "categorie": "SANTE",
        "types_cible": ["PERSONNE", "ZONE_SINISTREE", "ETABLISSEMENT"],
        "description": "Consultations gratuites, orientation et prise en charge avec une structure de santé.",
        "indicateurs": [
            _ind("type_consultation", "Type de consultation", "CHOIX", sensible=True,
                 choix=["Médecine générale", "Pédiatrie", "Ophtalmologie", "Dentaire", "Gynécologie",
                        "Dépistage", "Autre"]),
            _ind("pathologie", "Motif / pathologie", "TEXTE", sensible=True),
            _ind("medicaments_remis", "Médicaments remis", "BOOLEEN", sensible=True),
            _ind("oriente", "Orienté(e) vers une structure de santé", "BOOLEEN", sensible=True),
            _ind("cout_prise_en_charge", "Coût de la prise en charge", "MONTANT", unite="FCFA"),
            _ind("issue", "Issue de la prise en charge", "CHOIX", "SUIVI", sensible=True, obligatoire=True,
                 choix=["En traitement", "Guéri(e)", "Amélioré(e)", "Transféré(e)", "Perdu(e) de vue", "Décédé(e)"]),
            _ind("consultations_total", "Personnes consultées (total campagne)", "ENTIER", niveau="ACTION",
                 unite="personnes"),
            _ind("consultations_femmes", "dont femmes", "ENTIER", niveau="ACTION", unite="personnes"),
            _ind("consultations_enfants", "dont enfants de moins de 5 ans", "ENTIER", niveau="ACTION",
                 unite="enfants"),
        ],
    },
    {
        "code": "dons",
        "libelle": "Dons alimentaires et matériels",
        "categorie": "SOCIAL",
        "types_cible": ["PERSONNE", "GROUPE", "ASC", "ORGANISATION", "ETABLISSEMENT"],
        "description": "Distribution de vivres ou de matériel (ramadan, tabaski, soudure...).",
        "indicateurs": [
            _ind("nature_don", "Nature du don", "CHOIX", obligatoire=True,
                 choix=["Vivres", "Vêtements", "Matériel", "Argent", "Autre"]),
            _ind("quantite", "Quantité remise", "DECIMAL"),
            _ind("valeur_don", "Valeur du don", "MONTANT", unite="FCFA"),
            _ind("personnes_touchees", "Personnes touchées", "ENTIER", unite="personnes"),
            _ind("satisfaction", "Le don a répondu au besoin", "CHOIX", "SUIVI",
                 choix=["Pas du tout", "En partie", "Tout à fait"]),
        ],
    },
    {
        "code": "appui-activite",
        "libelle": "Appui à une activité génératrice de revenus",
        "categorie": "ECONOMIE",
        "types_cible": ["PERSONNE", "GROUPE", "ASC"],
        "description": "Aide à la création ou au renforcement d'un commerce ou d'un atelier.",
        "indicateurs": [
            _ind("activite", "Activité appuyée", "TEXTE", "REFERENCE", obligatoire=True),
            _ind("revenu_avant", "Revenu mensuel avant l'appui", "MONTANT", "REFERENCE", unite="FCFA"),
            _ind("montant_appui", "Montant de l'appui", "MONTANT", unite="FCFA", obligatoire=True),
            _ind("equipement", "Équipement fourni", "TEXTE"),
            _ind("activite_demarree", "Activité démarrée", "BOOLEEN", "SUIVI", obligatoire=True),
            _ind("activite_toujours_active", "Activité toujours en fonctionnement", "BOOLEEN", "SUIVI"),
            _ind("revenu_apres", "Revenu mensuel après l'appui", "MONTANT", "SUIVI", unite="FCFA"),
            _ind("emplois_crees", "Emplois créés", "ENTIER", "SUIVI"),
        ],
    },
    {
        "code": "sensibilisation",
        "libelle": "Sensibilisation / causerie",
        "categorie": "SOCIAL",
        "types_cible": ["GROUPE", "ASC", "ETABLISSEMENT", "ZONE_SINISTREE", "ORGANISATION"],
        "description": "Causeries, campagnes de sensibilisation (santé, hygiène, citoyenneté...).",
        "indicateurs": [
            _ind("theme", "Thème", "TEXTE", obligatoire=True),
            _ind("participants_femmes", "Participants - femmes", "ENTIER", unite="personnes"),
            _ind("participants_hommes", "Participants - hommes", "ENTIER", unite="personnes"),
            _ind("participants_jeunes", "dont jeunes (15-35 ans)", "ENTIER", unite="personnes"),
            _ind("supports_distribues", "Supports distribués", "ENTIER", unite="supports"),
        ],
    },
]

"""
adhesion/management/commands/creer_membres_test.py

Commande personnalisée : génère N membres de test (comptes, fiches
membres, sections si besoin, cartes signées) pour pouvoir tester
l'application sans tout créer à la main dans le shell.

Utilisation :
    python manage.py creer_membres_test
    python manage.py creer_membres_test --nombre 30
"""

import random

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from adhesion.models import Compte, Membre, Section, Carte
from adhesion.utils import generer_signature
import uuid as uuid_lib


# Données réalistes pour le contexte sénégalais du projet, plutôt que
# des noms génériques type "Test1", "Test2".
PRENOMS = [
    "Awa", "Fatou", "Mamadou", "Ibrahima", "Aissatou", "Moussa", "Khady",
    "Ousmane", "Aminata", "Cheikh", "Ndeye", "Abdoulaye", "Mariama",
    "Modou", "Sokhna", "Alioune", "Bineta", "Pape", "Astou", "Lamine",
]
NOMS = [
    "Diop", "Ndiaye", "Fall", "Sow", "Gueye", "Diallo", "Ba", "Sarr",
    "Cissé", "Faye", "Sy", "Thiam", "Diagne", "Kane", "Mbaye", "Sene",
]
SECTIONS = [
    ("Dakar-Plateau", "Dakar"),
    ("Pikine", "Pikine"),
    ("Thiès-Nord", "Thiès"),
    ("Saint-Louis", "Saint-Louis"),
    ("Ziguinchor", "Ziguinchor"),
]

MOT_DE_PASSE_TEST = "Test1234!"  # identique pour tous, affiché à la fin


class Command(BaseCommand):
    help = "Crée des membres de test avec comptes, cartes signées et sections"

    def add_arguments(self, parser):
        # Argument optionnel : python manage.py creer_membres_test --nombre 30
        parser.add_argument(
            "--nombre",
            type=int,
            default=20,
            help="Nombre de membres à créer (défaut : 20)",
        )

    def handle(self, *args, **options):
        nombre = options["nombre"]

        # 1. S'assurer que les sections existent (pas de doublon si la
        #    commande est relancée plusieurs fois : get_or_create).
        sections = []
        for nom_section, ville in SECTIONS:
            section, _ = Section.objects.get_or_create(
                nom_section=nom_section, defaults={"ville": ville}
            )
            sections.append(section)

        deja_utilises = set()
        crees = []

        for i in range(nombre):
            # Combinaison prénom/nom aléatoire, mais on évite les
            # doublons d'email en boucle pour rester réaliste.
            while True:
                prenom = random.choice(PRENOMS)
                nom = random.choice(NOMS)
                cle = f"{prenom}.{nom}"
                if cle not in deja_utilises:
                    deja_utilises.add(cle)
                    break

            email = f"{prenom.lower()}.{nom.lower()}{i}@parti-test.sn"

            compte = Compte(email=email, nom=nom, prenom=prenom)
            compte.definir_mot_de_passe(MOT_DE_PASSE_TEST)
            compte.save()

            numero_adherent = f"ADH-TEST-{i + 1:04d}"
            membre = Membre.objects.create(
                numero_adherent=numero_adherent,
                date_adhesion=timezone.now().date(),
                compte=compte,
                section=random.choice(sections),
            )

            nouvel_uuid = str(uuid_lib.uuid4())
            _, version = generer_signature(nouvel_uuid)
            Carte.objects.create(
                uuid=nouvel_uuid,
                type_carte=random.choice(["QR", "NFC"]),
                id_version_cle=version,
                membre=membre,
            )

            crees.append((f"{prenom} {nom}", email, numero_adherent))

        # Résumé affiché dans le terminal, pour copier-coller facilement
        # un email lors des tests.
        self.stdout.write(self.style.SUCCESS(f"\n{len(crees)} membres créés avec succès.\n"))
        self.stdout.write(f"Mot de passe commun à tous : {MOT_DE_PASSE_TEST}\n")
        self.stdout.write("-" * 70)
        for nom_complet, email, numero in crees:
            self.stdout.write(f"{numero}  {nom_complet:<25} {email}")
        self.stdout.write("-" * 70)
"""
Crée ou répare le compte super admin à partir des variables
d'environnement, sans jamais écrire d'identifiant dans le code.

Usage (exécutée à chaque déploiement, voir render.yaml) :
    AP2A_ADMIN_EMAIL=... AP2A_ADMIN_MOT_DE_PASSE=... python manage.py amorcer_super_admin

Sans ces deux variables, la commande ne fait RIEN : c'est le
fonctionnement normal. Procédure de récupération d'accès :
  1. définir les deux variables dans l'hébergeur (Render),
  2. redéployer, se connecter,
  3. SUPPRIMER les deux variables (sinon le mot de passe est reposé à
     chaque déploiement) et redéployer.
"""

import os
import uuid as uuid_lib

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from adhesion.models import Carte, Compte, Jeton, Membre


class Command(BaseCommand):
    help = "Crée ou répare le super admin depuis AP2A_ADMIN_EMAIL / AP2A_ADMIN_MOT_DE_PASSE."

    def handle(self, *args, **options):
        email = os.getenv("AP2A_ADMIN_EMAIL", "").strip()
        mot_de_passe = os.getenv("AP2A_ADMIN_MOT_DE_PASSE", "")

        if not email or not mot_de_passe:
            self.stdout.write("amorcer_super_admin : variables absentes, rien à faire.")
            return

        try:
            validate_password(mot_de_passe)
        except ValidationError as e:
            raise CommandError("AP2A_ADMIN_MOT_DE_PASSE refusé : " + " ".join(e.messages))

        with transaction.atomic():
            compte, cree = Compte.objects.get_or_create(
                email=email,
                defaults={"nom": "Admin", "prenom": "AP2A"},
            )
            compte.definir_mot_de_passe(mot_de_passe)
            compte.est_admin = True
            compte.est_super_admin = True
            compte.statut_compte = "ACTIF"
            compte.doit_changer_mot_de_passe = False
            compte.save()

            # Toute session ouverte avant la réparation est révoquée.
            Jeton.objects.filter(compte=compte).delete()

            if not Membre.objects.filter(compte=compte).exists():
                membre = Membre.objects.create(
                    compte=compte,
                    numero_adherent=f"ADH-{Membre.objects.count() + 1:04d}",
                    date_adhesion=timezone.now().date(),
                )
                Carte.objects.create(
                    uuid=str(uuid_lib.uuid4()),
                    type_carte="QR",
                    id_version_cle=settings.HMAC_VERSION_ACTIVE,
                    membre=membre,
                )

        self.stdout.write(self.style.WARNING(
            f"Super admin {'créé' if cree else 'réparé'} : {email}. "
            "Pensez à supprimer AP2A_ADMIN_EMAIL et AP2A_ADMIN_MOT_DE_PASSE de l'environnement."
        ))

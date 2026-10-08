"""
Commande Django pour créer le super admin initial du système AP2A.

Usage :
    python manage.py creer_super_admin

Si un super admin existe déjà, la commande informe et ne crée rien.
En mode non-interactif (--no-input), utilise les valeurs par défaut
ou les arguments passés en ligne de commande.
"""

import uuid as uuid_lib

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from adhesion.models import Carte, Compte, Membre, PermissionAdmin
from adhesion.utils import generer_mot_de_passe_temporaire


def _erreur_mot_de_passe(mot_de_passe):
    """Message d'erreur des validateurs Django (AUTH_PASSWORD_VALIDATORS), ou None."""
    try:
        validate_password(mot_de_passe)
    except ValidationError as e:
        return " ".join(e.messages)
    return None


class Command(BaseCommand):
    help = "Crée le super admin initial (fondateur) du système AP2A."

    def add_arguments(self, parser):
        parser.add_argument("--email", type=str, help="Email du super admin")
        parser.add_argument("--nom", type=str, help="Nom de famille")
        parser.add_argument("--prenom", type=str, help="Prénom")
        parser.add_argument("--mot-de-passe", type=str, help="Mot de passe (sera hashé)")
        parser.add_argument(
            "--fonction",
            type=str,
            default="PRESIDENT",
            help="Fonction AP2A du super admin (voir Membre.FONCTION_CHOICES, défaut : PRESIDENT)",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Mode non-interactif (utilise les arguments ou valeurs par défaut)",
        )

    def handle(self, *args, **options):
        # Vérifier si un super admin existe déjà
        super_admin_existant = Compte.objects.filter(est_super_admin=True).first()
        if super_admin_existant:
            self.stdout.write(
                self.style.WARNING(
                    f"Un super admin existe déjà : "
                    f"{super_admin_existant.prenom} {super_admin_existant.nom} "
                    f"<{super_admin_existant.email}>"
                )
            )
            self.stdout.write(
                "Pour des raisons de sécurité, il ne peut y avoir qu'un seul "
                "super admin. Utilisez l'interface pour modifier ses informations."
            )
            return

        if options["no_input"]:
            email = options.get("email") or "admin@ap2a.org"
            nom = options.get("nom") or "Admin"
            prenom = options.get("prenom") or "Super"
            # Jamais de mot de passe par défaut connu : sans argument, on
            # en tire un au hasard, affiché une seule fois ci-dessous.
            mot_de_passe = options.get("mot_de_passe")
            mot_de_passe_genere = not mot_de_passe
            if mot_de_passe_genere:
                mot_de_passe = generer_mot_de_passe_temporaire(16)
        else:
            self.stdout.write(self.style.MIGRATE_HEADING(
                "\n╔══════════════════════════════════════╗"
                "\n║   CRÉATION DU SUPER ADMIN AP2A      ║"
                "\n╚══════════════════════════════════════╝\n"
            ))

            email = input("Email du super admin : ").strip()
            while not email or "@" not in email:
                self.stdout.write(self.style.ERROR("Email invalide."))
                email = input("Email du super admin : ").strip()

            nom = input("Nom de famille : ").strip()
            while not nom:
                nom = input("Nom de famille : ").strip()

            prenom = input("Prénom : ").strip()
            while not prenom:
                prenom = input("Prénom : ").strip()

            import getpass
            mot_de_passe = getpass.getpass("Mot de passe : ")
            while (erreur := _erreur_mot_de_passe(mot_de_passe)):
                self.stdout.write(self.style.ERROR(erreur))
                mot_de_passe = getpass.getpass("Mot de passe : ")

            mot_de_passe_genere = False
            confirmation = getpass.getpass("Confirmer le mot de passe : ")
            while confirmation != mot_de_passe:
                self.stdout.write(self.style.ERROR("Les mots de passe ne correspondent pas."))
                mot_de_passe_genere = False
            confirmation = getpass.getpass("Confirmer le mot de passe : ")

        fonction = options.get("fonction") or "PRESIDENT"
        if fonction not in dict(Membre.FONCTION_CHOICES):
            self.stdout.write(self.style.ERROR(
                f"Fonction '{fonction}' inconnue. Choix possibles : "
                f"{', '.join(code for code, _ in Membre.FONCTION_CHOICES)}"
            ))
            return

        # Vérifier si l'email est déjà pris
        if Compte.objects.filter(email=email).exists():
            compte = Compte.objects.get(email=email)
            self.stdout.write(
                self.style.WARNING(
                    f"Le compte {email} existe déjà. "
                    f"Promotion en super admin..."
                )
            )
            compte.est_admin = True
            compte.est_super_admin = True
            compte.save(update_fields=["est_admin", "est_super_admin"])
        else:
            compte = Compte(
                email=email,
                nom=nom,
                prenom=prenom,
                est_admin=True,
                est_super_admin=True,
            )
            compte.definir_mot_de_passe(mot_de_passe)
            compte.doit_changer_mot_de_passe = mot_de_passe_genere
            compte.save()

        # Règle décidée ensemble : tout admin est forcément membre - le
        # super admin fondateur ne doit pas échapper à cette règle,
        # sinon il n'a ni fiche membre ni carte virtuelle dans l'app
        # (voir vue_nommer_admin, qui l'impose déjà pour les admins
        # nommés par la suite).
        if not hasattr(compte, "membre"):
            numero_adherent = f"ADH-{Membre.objects.count() + 1:04d}"
            membre = Membre.objects.create(
                numero_adherent=numero_adherent,
                date_adhesion=timezone.now().date(),
                compte=compte,
                fonction_association=fonction,
            )
            Carte.objects.create(
                uuid=str(uuid_lib.uuid4()),
                type_carte="QR",
                id_version_cle=settings.HMAC_VERSION_ACTIVE,
                membre=membre,
            )
            fiche_membre_msg = f"\n   Fiche membre : {numero_adherent} ({dict(Membre.FONCTION_CHOICES)[fonction]})"
        else:
            fiche_membre_msg = "\n   Fiche membre : déjà existante, inchangée"

        self.stdout.write(self.style.SUCCESS(
            # Pas d'emoji : la console Windows (cp1252) ne sait pas
            # toujours l'encoder et fait planter la commande après
            # coup, alors que les écritures en base ont déjà réussi.
            f"\nSuper admin créé avec succès !"
            f"\n   Email    : {compte.email}"
            f"\n   Nom      : {compte.prenom} {compte.nom}"
            f"\n   ID       : {compte.id_compte}"
            f"{fiche_membre_msg}"
            f"\n"
            + (
                f"\n   Mot de passe généré (affiché une seule fois) : {mot_de_passe}"
                f"\n   Il devra être changé à la première connexion."
                if mot_de_passe_genere else ""
            )
            + f"\n"
            f"\n   Ce compte a TOUS les droits et ne peut pas être destitué."
            f"\n   Il peut désigner d'autres admins et leur attribuer des permissions."
        ))

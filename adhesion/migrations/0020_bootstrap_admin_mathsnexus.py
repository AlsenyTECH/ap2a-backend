from django.db import migrations
from django.contrib.auth.hashers import make_password
from django.utils import timezone


EMAIL = "mathsnexus2024@gmail.com"
MOT_DE_PASSE = "Adfst2021."


def creer_ou_reparer_admin(apps, schema_editor):
    """
    Idempotent : si le compte existe déjà, on se contente de (re)poser
    le mot de passe et les droits admin - jamais d'erreur si la
    migration est rejouée (ex: redéploiement).
    """
    Compte = apps.get_model("adhesion", "Compte")
    Membre = apps.get_model("adhesion", "Membre")

    compte, cree = Compte.objects.get_or_create(
        email=EMAIL,
        defaults={
            "nom": "Admin",
            "prenom": "AP2A",
            "mot_de_passe_hash": make_password(MOT_DE_PASSE),
            "est_admin": True,
            "est_super_admin": True,
        },
    )
    if not cree:
        compte.mot_de_passe_hash = make_password(MOT_DE_PASSE)
        compte.est_admin = True
        compte.est_super_admin = True
        compte.doit_changer_mot_de_passe = False
        compte.save()

    if not Membre.objects.filter(compte=compte).exists():
        numero_adherent = f"ADH-{Membre.objects.count() + 1:04d}"
        Membre.objects.create(
            compte=compte,
            numero_adherent=numero_adherent,
            date_adhesion=timezone.now().date(),
        )


def _annuler(apps, schema_editor):
    pass  # irréversible par choix : on ne supprime jamais un compte admin via une migration


class Migration(migrations.Migration):

    dependencies = [
        ("adhesion", "0019_alter_permissionadmin_code_permission"),
    ]

    operations = [
        migrations.RunPython(creer_ou_reparer_admin, _annuler),
    ]

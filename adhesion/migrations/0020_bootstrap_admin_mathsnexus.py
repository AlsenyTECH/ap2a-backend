from django.db import migrations


def ne_rien_faire(apps, schema_editor):
    """
    Cette migration créait/réparait un compte super admin avec des
    identifiants écrits EN CLAIR dans le code source. Elle est
    désormais vide : un secret n'a rien à faire dans le dépôt.

    Pour créer ou réparer le super admin, utiliser la commande
    `python manage.py amorcer_super_admin`, qui lit les identifiants
    dans les variables d'environnement AP2A_ADMIN_EMAIL et
    AP2A_ADMIN_MOT_DE_PASSE.

    Le nom de la migration est conservé : elle est déjà enregistrée
    comme appliquée sur les bases existantes.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("adhesion", "0019_alter_permissionadmin_code_permission"),
    ]

    operations = [
        migrations.RunPython(ne_rien_faire, migrations.RunPython.noop),
    ]

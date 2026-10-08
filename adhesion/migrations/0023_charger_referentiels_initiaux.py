from django.db import migrations

from adhesion.referentiels_initiaux import REGIONS_DEPARTEMENTS, TYPES_ACTION


def charger(apps, schema_editor):
    """
    Idempotent : ne crée que ce qui manque, ne modifie jamais ce qu'un
    admin a pu changer ensuite.
    """
    Zone = apps.get_model("adhesion", "Zone")
    TypeAction = apps.get_model("adhesion", "TypeAction")
    DefinitionIndicateur = apps.get_model("adhesion", "DefinitionIndicateur")

    for nom_region, departements in REGIONS_DEPARTEMENTS.items():
        region, _ = Zone.objects.get_or_create(nom=nom_region, niveau="REGION", parent=None)
        for nom_departement in departements:
            Zone.objects.get_or_create(nom=nom_departement, niveau="DEPARTEMENT", parent=region)

    for donnees in TYPES_ACTION:
        champs = {k: v for k, v in donnees.items() if k not in ("code", "indicateurs")}
        type_action, _ = TypeAction.objects.get_or_create(code=donnees["code"], defaults=champs)
        for ordre, indicateur in enumerate(donnees["indicateurs"], start=1):
            champs_indicateur = {k: v for k, v in indicateur.items() if k != "code"}
            DefinitionIndicateur.objects.get_or_create(
                type_action=type_action, code=indicateur["code"],
                defaults={**champs_indicateur, "ordre": ordre},
            )


class Migration(migrations.Migration):

    dependencies = [
        ("adhesion", "0022_referentiels_suivi_actions"),
    ]

    operations = [
        migrations.RunPython(charger, migrations.RunPython.noop),
    ]

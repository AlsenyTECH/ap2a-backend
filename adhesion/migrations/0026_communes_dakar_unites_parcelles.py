from django.db import migrations

from adhesion.referentiels_initiaux import COMMUNES, QUARTIERS


def charger(apps, schema_editor):
    """Communes du département de Dakar et unités des Parcelles Assainies. Idempotent."""
    Zone = apps.get_model("adhesion", "Zone")

    def zone(*noms):
        niveaux = ["REGION", "DEPARTEMENT", "COMMUNE", "QUARTIER"]
        parent = None
        for niveau, nom in zip(niveaux, noms):
            parent, _ = Zone.objects.get_or_create(nom=nom, niveau=niveau, parent=parent)
        return parent

    for (region, departement), communes in COMMUNES.items():
        for commune in communes:
            zone(region, departement, commune)
    for (region, departement, commune), quartiers in QUARTIERS.items():
        for quartier in quartiers:
            zone(region, departement, commune, quartier)


class Migration(migrations.Migration):

    dependencies = [
        ("adhesion", "0025_reprendre_beneficiaires_en_cibles"),
    ]

    operations = [
        migrations.RunPython(charger, migrations.RunPython.noop),
    ]

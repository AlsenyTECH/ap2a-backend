from django.db import migrations

from adhesion.cibles import normaliser_telephone, normaliser_texte


def reprendre(apps, schema_editor):
    """
    Chaque bénéficiaire des anciennes actions sociales devient une cible
    PERSONNE, liée par beneficiaire_origine (son historique d'actions
    reste ainsi visible sur sa fiche). Idempotent.
    """
    Beneficiaire = apps.get_model("adhesion", "Beneficiaire")
    Cible = apps.get_model("adhesion", "Cible")

    membres_pris = set(Cible.objects.exclude(membre=None).values_list("membre_id", flat=True))
    for b in Beneficiaire.objects.filter(cible__isnull=True):
        membre_id = b.membre_id if b.membre_id and b.membre_id not in membres_pris else None
        if membre_id:
            membres_pris.add(membre_id)
        Cible.objects.create(
            type_cible="PERSONNE",
            nom=b.nom,
            prenom=b.prenom,
            sexe=b.sexe or "",
            date_naissance=b.date_naissance,
            numero_identification=b.numero_identification or "",
            telephone=b.telephone or "",
            telephone_normalise=normaliser_telephone(b.telephone),
            nom_normalise=normaliser_texte(b.nom),
            prenom_normalise=normaliser_texte(b.prenom),
            adresse=(b.adresse or "")[:255],
            membre_id=membre_id,
            beneficiaire_origine=b,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("adhesion", "0024_cibles"),
    ]

    operations = [
        migrations.RunPython(reprendre, migrations.RunPython.noop),
    ]

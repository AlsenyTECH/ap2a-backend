import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def hacher_jetons_existants(apps, schema_editor):
    """
    Les jetons existants étaient stockés en clair dans `cle` (renommé
    `empreinte` juste avant) : on les remplace par leur empreinte
    SHA-256 pour que les sessions en cours restent valides, avec une
    expiration comptée à partir de maintenant.
    """
    Jeton = apps.get_model("adhesion", "Jeton")
    expiration = timezone.now() + timedelta(hours=settings.JETON_DUREE_VIE_HEURES)
    for jeton in Jeton.objects.all():
        jeton.empreinte = hashlib.sha256(jeton.empreinte.encode("utf-8")).hexdigest()
        jeton.date_expiration = expiration
        jeton.save(update_fields=["empreinte", "date_expiration"])


class Migration(migrations.Migration):

    dependencies = [
        ("adhesion", "0020_bootstrap_admin_mathsnexus"),
    ]

    operations = [
        migrations.RenameField(model_name="jeton", old_name="cle", new_name="empreinte"),
        migrations.AddField(
            model_name="jeton",
            name="date_expiration",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(hacher_jetons_existants, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="jeton",
            name="date_expiration",
            field=models.DateTimeField(),
        ),
        migrations.CreateModel(
            name="TentativeConnexion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identifiant", models.CharField(db_index=True, max_length=150)),
                ("adresse_ip", models.GenericIPAddressField(blank=True, db_index=True, null=True)),
                ("date_tentative", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"db_table": "TENTATIVE_CONNEXION"},
        ),
        migrations.CreateModel(
            name="TicketScanConsomme",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nonce", models.CharField(max_length=64, unique=True)),
                ("date_consommation", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"db_table": "TICKET_SCAN_CONSOMME"},
        ),
    ]

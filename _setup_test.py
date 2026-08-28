import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from datetime import date, timedelta
from adhesion.models import Formation, Cohorte, Participant, InscriptionCohorte
import uuid as uuid_lib
from adhesion.utils import generer_signature

Formation.objects.filter(titre__startswith="TEST-CYCLE-VIE").delete()

f = Formation.objects.create(titre="TEST-CYCLE-VIE Formation", statut="BROUILLON")

c_future = Cohorte.objects.create(
    formation=f, code_cohorte="TEST-CYCLE-VIE-FUTURE",
    date_debut=date.today() + timedelta(days=10),
    date_fin=date.today() + timedelta(days=20),
    lieu="Salle Test",
)
print("Cohorte future statut par defaut:", c_future.statut)

c_passee = Cohorte.objects.create(
    formation=f, code_cohorte="TEST-CYCLE-VIE-PASSEE",
    date_debut=date.today() - timedelta(days=10),
    date_fin=date.today() - timedelta(days=1),
    lieu="Salle Test",
    statut="PROGRAMMEE",
)

nouvel_uuid = uuid_lib.uuid4()
_, version = generer_signature(str(nouvel_uuid))
p = Participant.objects.create(
    nom="Dupont", prenom="Testeur", telephone="770000001",
    uuid=nouvel_uuid, id_version_cle=version,
)
InscriptionCohorte.objects.create(participant=p, cohorte=c_passee, source="MANUEL")

print("id_formation:", f.id_formation)
print("id_cohorte_future:", c_future.id_cohorte)
print("id_cohorte_passee:", c_passee.id_cohorte)

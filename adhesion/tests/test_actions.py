"""Actions : création à partir des besoins, cycle de vie, cibles, indicateurs, équipe, volontariat, calendrier."""

import datetime

from django.test import TestCase
from django.utils import timezone

from adhesion.indicateurs import colonnes_depuis_saisie
from adhesion.models import (
    Action, BesoinCible, Cible, DefinitionIndicateur, MembreEquipe, Notification, Partenaire, TypeAction,
    ValeurIndicateur,
)

from .outils import client_pour, creer_compte, creer_membre


def demain(jours=1):
    return (timezone.localdate() + datetime.timedelta(days=jours)).isoformat()


class BaseActions(TestCase):

    def setUp(self):
        self.admin = creer_compte(est_admin=True, permissions=["GERER_ACTIONS_SOCIALES", "GERER_CIBLES"])
        self.client_admin = client_pour(self.admin)
        self.kits = TypeAction.objects.get(code="fournitures-scolaires")
        self.ecole = Cible.objects.create(type_cible="ETABLISSEMENT", nom="École Unité 17")
        self.eleve = Cible.objects.create(type_cible="PERSONNE", nom="Sow", prenom="Moussa")

    def creer_action(self, **donnees):
        corps = {"titre": "Kits rentrée 2026", "type_action": self.kits.pk, "date_debut": demain(), **donnees}
        reponse = self.client_admin.post("/api/admin/actions/", corps, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.data)
        return reponse.data

    def url(self, action, suite=""):
        return f"/api/admin/actions/{action['id_action']}/{suite}"


class CreationEtCycleDeVieTests(BaseActions):

    def test_creation_depuis_les_besoins(self):
        besoin = BesoinCible.objects.create(cible=self.ecole, description="Fournitures pour 640 élèves", type_action=self.kits)
        action = self.creer_action(besoins=[besoin.pk], cibles=[self.eleve.pk, self.ecole.pk])
        self.assertEqual(len(action["cibles"]), 2)
        self.assertEqual(next(c for c in action["cibles"] if c["id_cible"] == self.ecole.pk)["besoin"], besoin.description)
        besoin.refresh_from_db()
        self.assertEqual(besoin.statut, "PLANIFIE")

    def test_fin_avant_debut_refusee(self):
        reponse = self.client_admin.post(
            "/api/admin/actions/",
            {"titre": "X", "type_action": self.kits.pk, "date_debut": demain(5), "date_fin": demain(1)},
            format="json",
        )
        self.assertEqual(reponse.status_code, 400)

    def test_transitions(self):
        action = self.creer_action()
        self.assertEqual(self.client_admin.post(self.url(action, "statut/"), {"statut": "TERMINEE"}).status_code, 400)
        for statut in ("PLANIFIEE", "EN_COURS", "TERMINEE"):
            self.assertEqual(self.client_admin.post(self.url(action, "statut/"), {"statut": statut}).status_code, 200)
        reponse = self.client_admin.patch(self.url(action), {"titre": "Nouveau"}, format="json")
        self.assertEqual(reponse.status_code, 400)
        reponse = self.client_admin.patch(self.url(action), {"bilan": "Tout s'est bien passé"}, format="json")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self.client_admin.delete(self.url(action)).status_code, 409)

    def test_besoins_couverts_a_la_cloture(self):
        servi = BesoinCible.objects.create(cible=self.ecole, description="Kits")
        non_servi = BesoinCible.objects.create(cible=self.eleve, description="Kits")
        action = self.creer_action(besoins=[servi.pk, non_servi.pk])
        ligne = next(c for c in action["cibles"] if c["id_cible"] == self.ecole.pk)
        reponse = self.client_admin.patch(f"/api/admin/action-cibles/{ligne['id_action_cible']}/", {"statut": "SERVIE"}, format="json")
        self.assertIsNotNone(next(c for c in reponse.data["cibles"] if c["id_cible"] == self.ecole.pk)["date_intervention"])
        for statut in ("PLANIFIEE", "EN_COURS", "TERMINEE"):
            self.client_admin.post(self.url(action, "statut/"), {"statut": statut})
        servi.refresh_from_db(); non_servi.refresh_from_db()
        self.assertEqual((servi.statut, non_servi.statut), ("COUVERT", "PLANIFIE"))

    def test_annulation_libere_les_besoins(self):
        besoin = BesoinCible.objects.create(cible=self.ecole, description="Kits")
        action = self.creer_action(besoins=[besoin.pk])
        self.client_admin.post(self.url(action, "statut/"), {"statut": "ANNULEE"})
        besoin.refresh_from_db()
        self.assertEqual(besoin.statut, "IDENTIFIE")

    def test_suppression_en_preparation(self):
        action = self.creer_action(cibles=[self.ecole.pk])
        self.assertEqual(self.client_admin.delete(self.url(action)).status_code, 204)
        self.assertFalse(Action.objects.exists())

    def test_liste_et_filtres(self):
        self.creer_action(cibles=[self.ecole.pk])
        self.creer_action(titre="Autre", type_action=TypeAction.objects.get(code="dons").pk)
        liste = self.client_admin.get("/api/admin/actions/").data
        self.assertEqual(liste["total"], 2)
        self.assertEqual(self.client_admin.get("/api/admin/actions/", {"type": self.kits.pk}).data["total"], 1)
        self.assertEqual(self.client_admin.get("/api/admin/actions/", {"q": "autre"}).data["total"], 1)
        kits = next(a for a in liste["resultats"] if a["titre"].startswith("Kits"))
        self.assertEqual(kits["nombre_cibles"], 1)

    def test_permissions(self):
        lecteur = client_pour(creer_compte(est_admin=True, permissions=["VOIR_RAPPORTS"]))
        self.assertEqual(lecteur.get("/api/admin/actions/").status_code, 200)
        self.assertEqual(
            lecteur.post("/api/admin/actions/", {"titre": "X", "type_action": self.kits.pk, "date_debut": demain()}, format="json").status_code,
            403,
        )
        self.assertEqual(client_pour(creer_membre().compte).get("/api/admin/actions/").status_code, 403)

    def test_historique_et_fusion_de_cible(self):
        action = self.creer_action(cibles=[self.eleve.pk])
        fiche = self.client_admin.get(f"/api/admin/cibles/{self.eleve.pk}/").data
        self.assertEqual(fiche["historique"][0]["titre"], "Kits rentrée 2026")
        doublon = Cible.objects.create(type_cible="PERSONNE", nom="Sow", prenom="Moussa")
        BesoinCible.objects.create(cible=doublon, description="Lunettes")
        self.client_admin.post(f"/api/admin/actions/{action['id_action']}/cibles/", {"cibles": [doublon.pk]}, format="json")
        self.client_admin.post(f"/api/admin/cibles/{self.eleve.pk}/fusionner/", {"id_doublon": doublon.pk})
        self.assertEqual(Action.objects.get().cibles.count(), 1)
        self.assertEqual(self.eleve.besoins.count(), 1)


class IndicateursTests(BaseActions):

    def test_conversion(self):
        kits = DefinitionIndicateur.objects.get(type_action=self.kits, code="kits_distribues")
        self.assertEqual(colonnes_depuis_saisie(kits, "12")["valeur_nombre"], 12)
        for invalide in ("douze", "1.5", "-3"):
            with self.assertRaises(ValueError):
                colonnes_depuis_saisie(kits, invalide)
        critere = DefinitionIndicateur.objects.get(type_action=self.kits, code="critere_selection")
        with self.assertRaises(ValueError):
            colonnes_depuis_saisie(critere, "Inconnu")

    def test_saisie_par_cible_et_par_action(self):
        dons = TypeAction.objects.get(code="campagne-medicale")
        action = self.creer_action(type_action=dons.pk, cibles=[self.eleve.pk])
        ligne = action["cibles"][0]["id_action_cible"]
        cout = DefinitionIndicateur.objects.get(type_action=dons, code="cout_prise_en_charge")
        total = DefinitionIndicateur.objects.get(type_action=dons, code="consultations_total")
        reponse = self.client_admin.put(self.url(action, "valeurs/"), {"valeurs": [
            {"indicateur": cout.pk, "action_cible": ligne, "valeur": "15 000"},
            {"indicateur": total.pk, "action_cible": None, "valeur": 120},
        ]}, format="json")
        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertEqual(reponse.data["cibles"][0]["valeurs"][str(cout.pk)], 15000)
        self.assertEqual(reponse.data["valeurs_action"][str(total.pk)], 120)
        # Niveau incohérent : refusé, rien n'est enregistré.
        reponse = self.client_admin.put(self.url(action, "valeurs/"), {"valeurs": [
            {"indicateur": cout.pk, "action_cible": ligne, "valeur": 1},
            {"indicateur": total.pk, "action_cible": ligne, "valeur": 1},
        ]}, format="json")
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(ValeurIndicateur.objects.get(indicateur=cout).valeur_nombre, 15000)
        # Valeur vide : effacée.
        self.client_admin.put(self.url(action, "valeurs/"), {"valeurs": [
            {"indicateur": total.pk, "action_cible": None, "valeur": ""}]}, format="json")
        self.assertFalse(ValeurIndicateur.objects.filter(indicateur=total).exists())

    def test_indicateurs_medicaux_proteges(self):
        medical = TypeAction.objects.get(code="campagne-medicale")
        action = self.creer_action(type_action=medical.pk, cibles=[self.eleve.pk])
        ligne = action["cibles"][0]["id_action_cible"]
        issue = DefinitionIndicateur.objects.get(type_action=medical, code="issue")
        self.assertNotIn("issue", [i["code"] for i in action["indicateurs"]])
        self.assertTrue(action["indicateurs_masques"])
        reponse = self.client_admin.put(self.url(action, "valeurs/"), {"valeurs": [
            {"indicateur": issue.pk, "action_cible": ligne, "valeur": "Guéri(e)"}]}, format="json")
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("Données médicales", reponse.data["erreur"])

        medecin_admin = client_pour(creer_compte(
            est_admin=True, permissions=["GERER_ACTIONS_SOCIALES", "DONNEES_MEDICALES"]))
        reponse = medecin_admin.put(self.url(action, "valeurs/"), {"valeurs": [
            {"indicateur": issue.pk, "action_cible": ligne, "valeur": "Guéri(e)"}]}, format="json")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data["cibles"][0]["valeurs"][str(issue.pk)], "Guéri(e)")
        # Sans la permission, la valeur saisie reste invisible.
        fiche = self.client_admin.get(self.url(action)).data
        self.assertNotIn(str(issue.pk), fiche["cibles"][0]["valeurs"])


class EquipeEtVolontariatTests(BaseActions):

    def test_affectation_et_convocation(self):
        membre = creer_membre()
        action = self.creer_action()
        reponse = self.client_admin.post(self.url(action, "equipe/"), {"membres": [membre.pk], "role": "Logistique"}, format="json")
        self.assertEqual(reponse.data["equipe"][0]["statut"], "CONFIRME")
        self.assertEqual(Notification.objects.filter(compte_destinataire=membre.compte).count(), 1)
        self.client_admin.post(self.url(action, "statut/"), {"statut": "PLANIFIEE"})
        self.assertEqual(Notification.objects.filter(compte_destinataire=membre.compte).count(), 2)

    def test_volontariat(self):
        responsable, volontaire = creer_membre(), creer_membre()
        action = self.creer_action(appel_volontaires=True, responsable=responsable.pk)
        client_volontaire = client_pour(volontaire.compte)
        appels = client_volontaire.get("/api/membre/actions/").data["appels"]
        self.assertEqual([a["id_action"] for a in appels], [action["id_action"]])
        self.assertEqual(client_volontaire.post(f"/api/membre/actions/{action['id_action']}/volontaire/").status_code, 201)
        self.assertEqual(client_volontaire.post(f"/api/membre/actions/{action['id_action']}/volontaire/").status_code, 409)
        self.assertTrue(Notification.objects.filter(compte_destinataire=responsable.compte).exists())
        mes = client_volontaire.get("/api/membre/actions/").data
        self.assertEqual((mes["appels"], mes["missions"][0]["statut_equipe"]), ([], "PROPOSE"))

        ligne = MembreEquipe.objects.get(membre=volontaire)
        self.client_admin.patch(f"/api/admin/equipe/{ligne.pk}/", {"statut": "CONFIRME", "role": "Accueil"}, format="json")
        self.assertTrue(Notification.objects.filter(compte_destinataire=volontaire.compte, titre__startswith="Candidature retenue").exists())

        self.assertEqual(client_volontaire.delete(f"/api/membre/actions/{action['id_action']}/volontaire/").status_code, 204)
        ligne.refresh_from_db()
        self.assertEqual(ligne.statut, "DECLINE")

    def test_volontariat_ferme(self):
        action = self.creer_action()
        reponse = client_pour(creer_membre().compte).post(f"/api/membre/actions/{action['id_action']}/volontaire/")
        self.assertEqual(reponse.status_code, 400)

    def test_taches_et_partenaires(self):
        action = self.creer_action()
        reponse = self.client_admin.post(self.url(action, "taches/"), {"titre": "Acheter les cahiers", "phase": "AVANT"}, format="json")
        tache = reponse.data["taches"][0]
        reponse = self.client_admin.patch(f"/api/admin/taches/{tache['id_tache']}/", {"faite": True}, format="json")
        self.assertTrue(reponse.data["taches"][0]["faite"])
        partenaire = Partenaire.objects.create(nom="3FPT", type_partenaire="FORMATION")
        reponse = self.client_admin.post(self.url(action, "partenaires/"), {"partenaire": partenaire.pk, "role": "FINANCEUR"}, format="json")
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(
            self.client_admin.post(self.url(action, "partenaires/"), {"partenaire": partenaire.pk}, format="json").status_code, 409
        )


class BesoinsEtCalendrierTests(BaseActions):

    def test_besoins(self):
        reponse = self.client_admin.post(
            f"/api/admin/cibles/{self.ecole.pk}/besoins/",
            {"description": "Toilettes à refaire", "priorite": "URGENTE", "type_action": TypeAction.objects.get(code="rehabilitation-ecole").pk},
            format="json",
        )
        self.assertEqual(reponse.status_code, 201)
        BesoinCible.objects.create(cible=self.eleve, description="Kits", priorite="BASSE")
        besoins = self.client_admin.get("/api/admin/besoins/", {"statut": "IDENTIFIE"}).data
        self.assertEqual([b["priorite"] for b in besoins], ["URGENTE", "BASSE"])
        fiche = self.client_admin.get(f"/api/admin/cibles/{self.ecole.pk}/").data
        self.assertEqual(fiche["besoins"][0]["description"], "Toilettes à refaire")

    def test_calendrier(self):
        self.creer_action(date_debut=demain(2), date_fin=demain(4))
        annulee = self.creer_action(titre="Annulée", date_debut=demain(3))
        self.client_admin.post(self.url(annulee, "statut/"), {"statut": "ANNULEE"})
        reponse = self.client_admin.get("/api/admin/calendrier/", {"debut": demain(3), "fin": demain(10)})
        self.assertEqual([e["titre"] for e in reponse.data], ["Kits rentrée 2026"])
        self.assertEqual(self.client_admin.get("/api/admin/calendrier/", {"debut": demain(10), "fin": demain(1)}).status_code, 400)

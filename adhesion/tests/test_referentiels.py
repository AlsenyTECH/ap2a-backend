"""Référentiels du suivi des actions : zones, partenaires, types d'action, indicateurs."""

from django.test import TestCase
from django.utils import timezone

from adhesion.models import (
    ActionSociale, Beneficiaire, DefinitionIndicateur, DetailMedical, JournalAudit, ParticipationAction,
    Partenaire, TypeAction, Zone,
)
from adhesion.referentiels_initiaux import REGIONS_DEPARTEMENTS, TYPES_ACTION

from .outils import client_pour, creer_compte


class DonneesInitialesTests(TestCase):
    """Chargées par la migration 0023 (présentes dans la base de test)."""

    def test_decoupage_du_senegal(self):
        self.assertEqual(Zone.objects.filter(niveau="REGION").count(), 14)
        self.assertEqual(Zone.objects.filter(niveau="DEPARTEMENT").count(), 46)
        self.assertEqual(sum(len(d) for d in REGIONS_DEPARTEMENTS.values()), 46)
        pikine = Zone.objects.get(nom="Pikine", niveau="DEPARTEMENT")
        self.assertEqual(pikine.chemin(), "Dakar > Pikine")

    def test_communes_de_dakar_et_unites_des_parcelles(self):
        dakar = Zone.objects.get(nom="Dakar", niveau="DEPARTEMENT")
        self.assertEqual(dakar.sous_zones.filter(niveau="COMMUNE").count(), 19)
        unites = Zone.objects.filter(parent__nom="Parcelles Assainies", niveau="QUARTIER")
        self.assertEqual(unites.count(), 26)
        self.assertEqual(
            Zone.objects.get(nom="Unité 17").chemin(), "Dakar > Dakar > Parcelles Assainies > Unité 17"
        )

    def test_types_et_indicateurs(self):
        self.assertEqual(TypeAction.objects.count(), len(TYPES_ACTION))
        medical = TypeAction.objects.get(code="campagne-medicale")
        self.assertTrue(medical.indicateurs.filter(sensible=True).exists())
        for indicateur in DefinitionIndicateur.objects.filter(type_valeur="CHOIX"):
            self.assertGreaterEqual(len(indicateur.choix), 2, indicateur)
        for indicateur in DefinitionIndicateur.objects.exclude(type_valeur="CHOIX"):
            self.assertEqual(indicateur.choix, [], indicateur)


class ZonesTests(TestCase):

    def setUp(self):
        self.gestionnaire = client_pour(creer_compte(est_admin=True, permissions=["GERER_REFERENTIELS"]))
        self.pikine = Zone.objects.get(nom="Pikine", niveau="DEPARTEMENT")

    def creer(self, client=None, **donnees):
        return (client or self.gestionnaire).post("/api/admin/zones/", donnees, format="json")

    def test_lecture_par_tout_compte_connecte(self):
        client = client_pour(creer_compte())
        regions = client.get("/api/referentiels/zones/").data
        self.assertEqual(len(regions), 14)
        dakar = next(r for r in regions if r["nom"] == "Dakar")
        self.assertEqual(dakar["nombre_sous_zones"], 5)
        departements = client.get("/api/referentiels/zones/", {"parent": dakar["id_zone"]}).data
        self.assertEqual({d["nom"] for d in departements}, set(REGIONS_DEPARTEMENTS["Dakar"]))
        recherche = client.get("/api/referentiels/zones/", {"q": "pik"}).data
        self.assertEqual([z["chemin"] for z in recherche], ["Dakar > Pikine"])

    def test_recherche_sans_accents_et_abreviation(self):
        client = client_pour(creer_compte())
        chemins = lambda q: [z["chemin"] for z in client.get("/api/referentiels/zones/", {"q": q}).data]
        self.assertIn("Dakar > Dakar > Parcelles Assainies > Unité 17", chemins("unite 17"))
        self.assertEqual(chemins("U17"), ["Dakar > Dakar > Parcelles Assainies > Unité 17"])
        self.assertEqual(chemins("u 1"), ["Dakar > Dakar > Parcelles Assainies > Unité 1"])
        self.assertIn("Dakar > Dakar > Cambérène", chemins("camberene"))

    def test_creation_commune_et_quartier(self):
        commune = self.creer(nom="Thiaroye-sur-Mer", niveau="COMMUNE", parent=self.pikine.id_zone)
        self.assertEqual(commune.status_code, 201)
        self.assertEqual(commune.data["chemin"], "Dakar > Pikine > Thiaroye-sur-Mer")
        quartier = self.creer(nom="Darou Salam", niveau="QUARTIER", parent=commune.data["id_zone"])
        self.assertEqual(quartier.status_code, 201)
        self.assertTrue(JournalAudit.objects.filter(type_action="CREATION_ZONE").exists())

    def test_hierarchie_respectee(self):
        region = Zone.objects.get(nom="Dakar", niveau="REGION")
        reponse = self.creer(nom="Mauvais", niveau="COMMUNE", parent=region.id_zone)
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("département", reponse.data["erreur"])
        self.assertEqual(self.creer(nom="Orpheline", niveau="COMMUNE").status_code, 400)
        self.assertEqual(self.creer(nom="Région", niveau="REGION", parent=region.id_zone).status_code, 400)

    def test_doublons_refuses(self):
        self.assertEqual(self.creer(nom="pikine", niveau="DEPARTEMENT", parent=self.pikine.parent_id).status_code, 400)
        self.assertEqual(self.creer(nom="dakar", niveau="REGION").status_code, 400)

    def test_suppression_protegee(self):
        reponse = self.gestionnaire.delete(f"/api/admin/zones/{self.pikine.parent_id}/")
        self.assertEqual(reponse.status_code, 409)
        commune = self.creer(nom="Yeumbeul", niveau="COMMUNE", parent=self.pikine.id_zone).data
        self.assertEqual(self.gestionnaire.delete(f"/api/admin/zones/{commune['id_zone']}/").status_code, 204)

    def test_ecriture_reservee(self):
        autre_admin = client_pour(creer_compte(est_admin=True, permissions=["GERER_MEMBRES"]))
        self.assertEqual(
            self.creer(client=autre_admin, nom="X", niveau="COMMUNE", parent=self.pikine.id_zone).status_code, 403
        )
        self.assertEqual(autre_admin.patch(f"/api/admin/zones/{self.pikine.id_zone}/", {"nom": "Y"}).status_code, 403)


class PartenairesTests(TestCase):

    def setUp(self):
        self.gestionnaire = client_pour(creer_compte(est_admin=True, permissions=["GERER_REFERENTIELS"]))

    def creer(self, client=None, **donnees):
        donnees = {"nom": "Centre de formation Don Bosco", "type_partenaire": "FORMATION", **donnees}
        return (client or self.gestionnaire).post("/api/admin/partenaires/", donnees, format="json")

    def test_creation_et_lecture(self):
        zone = Zone.objects.get(nom="Thiès", niveau="DEPARTEMENT")
        reponse = self.creer(sigle="CFDB", zone=zone.id_zone, email="contact@exemple.org")
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(reponse.data["zone_chemin"], "Thiès > Thiès")
        lecteur = client_pour(creer_compte(est_admin=True, permissions=["GERER_EVENEMENTS"]))
        self.assertEqual(len(lecteur.get("/api/admin/partenaires/").data), 1)
        self.assertEqual(len(lecteur.get("/api/admin/partenaires/", {"q": "cfdb"}).data), 1)

    def test_lecture_reservee_aux_admins(self):
        self.creer()
        self.assertEqual(client_pour(creer_compte()).get("/api/admin/partenaires/").status_code, 403)

    def test_ecriture_reservee(self):
        lecteur = client_pour(creer_compte(est_admin=True, permissions=["GERER_EVENEMENTS"]))
        self.assertEqual(self.creer(client=lecteur).status_code, 403)
        partenaire = self.creer().data
        url = f"/api/admin/partenaires/{partenaire['id_partenaire']}/"
        self.assertEqual(lecteur.patch(url, {"actif": False}, format="json").status_code, 403)
        self.assertEqual(lecteur.delete(url).status_code, 403)

    def test_validation(self):
        self.creer()
        reponse = self.creer(nom="centre de formation don bosco")
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("erreur", reponse.data)
        self.assertEqual(self.creer(nom="Autre", type_partenaire="INCONNU").status_code, 400)
        self.assertEqual(self.creer(nom="Autre", email="pas-un-email").status_code, 400)

    def test_desactivation(self):
        partenaire = self.creer().data
        url = f"/api/admin/partenaires/{partenaire['id_partenaire']}/"
        self.assertEqual(self.gestionnaire.patch(url, {"actif": False}, format="json").status_code, 200)
        self.assertEqual(len(self.gestionnaire.get("/api/admin/partenaires/").data), 0)
        self.assertEqual(len(self.gestionnaire.get("/api/admin/partenaires/", {"inclure_inactifs": "1"}).data), 1)


class TypesActionTests(TestCase):

    def setUp(self):
        self.gestionnaire = client_pour(creer_compte(est_admin=True, permissions=["GERER_REFERENTIELS"]))

    def creer_type(self, **donnees):
        donnees = {"libelle": "Reboisement", "categorie": "AUTRE", "types_cible": ["GROUPE", "ZONE_SINISTREE"], **donnees}
        return self.gestionnaire.post("/api/admin/types-action/", donnees, format="json")

    def test_lecture_avec_indicateurs(self):
        types = client_pour(creer_compte()).get("/api/referentiels/types-action/").data
        kits = next(t for t in types if t["code"] == "fournitures-scolaires")
        self.assertTrue(any(i["code"] == "kits_distribues" for i in kits["indicateurs"]))

    def test_creation_avec_code_genere(self):
        reponse = self.creer_type()
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(reponse.data["code"], "reboisement")
        self.assertEqual(self.creer_type().data["code"], "reboisement-2")

    def test_types_cible_valides(self):
        self.assertEqual(self.creer_type(types_cible=[]).status_code, 400)
        self.assertEqual(self.creer_type(types_cible=["PLANETE"]).status_code, 400)

    def test_indicateurs(self):
        type_action = self.creer_type().data
        url = f"/api/admin/types-action/{type_action['id_type_action']}/indicateurs/"
        arbres = self.gestionnaire.post(
            url, {"libelle": "Arbres plantés", "type_valeur": "ENTIER", "unite": "arbres"}, format="json"
        )
        self.assertEqual(arbres.status_code, 201)
        self.assertEqual(arbres.data["code"], "arbres-plantes")
        self.assertEqual(arbres.data["ordre"], 1)

        survie = self.gestionnaire.post(
            url,
            {"libelle": "Taux de survie", "type_valeur": "CHOIX", "moment": "SUIVI",
             "choix": ["Faible", "Moyen", "Élevé"]},
            format="json",
        )
        self.assertEqual(survie.status_code, 201)
        self.assertEqual(survie.data["ordre"], 2)

    def test_regles_des_choix(self):
        type_action = self.creer_type().data
        url = f"/api/admin/types-action/{type_action['id_type_action']}/indicateurs/"
        for corps in (
            {"libelle": "A", "type_valeur": "CHOIX", "choix": ["Seul"]},
            {"libelle": "B", "type_valeur": "CHOIX", "choix": ["Oui", "oui"]},
            {"libelle": "C", "type_valeur": "ENTIER", "choix": ["1", "2"]},
            {"libelle": "D", "type_valeur": "CHOIX", "choix": "Oui,Non"},
        ):
            with self.subTest(corps=corps):
                self.assertEqual(self.gestionnaire.post(url, corps, format="json").status_code, 400)

    def test_code_indicateur_unique_par_type(self):
        type_action = TypeAction.objects.get(code="dons")
        reponse = self.gestionnaire.post(
            f"/api/admin/types-action/{type_action.id_type_action}/indicateurs/",
            {"libelle": "Doublon", "code": "valeur_don", "type_valeur": "MONTANT"},
            format="json",
        )
        self.assertEqual(reponse.status_code, 400)

    def test_modification_et_suppression_indicateur(self):
        indicateur = DefinitionIndicateur.objects.get(type_action__code="dons", code="quantite")
        url = f"/api/admin/indicateurs/{indicateur.id_indicateur}/"
        reponse = self.gestionnaire.patch(url, {"unite": "kg", "actif": False}, format="json")
        self.assertEqual(reponse.status_code, 200)
        types = self.gestionnaire.get("/api/referentiels/types-action/").data
        dons = next(t for t in types if t["code"] == "dons")
        self.assertNotIn("quantite", [i["code"] for i in dons["indicateurs"]])
        self.assertEqual(self.gestionnaire.delete(url).status_code, 204)

    def test_ecriture_reservee(self):
        autre = client_pour(creer_compte(est_admin=True, permissions=["GERER_ACTIONS_SOCIALES"]))
        self.assertEqual(autre.post("/api/admin/types-action/", {"libelle": "X"}, format="json").status_code, 403)


class DonneesMedicalesTests(TestCase):

    def setUp(self):
        organisateur = creer_compte(est_super_admin=True)
        self.action = ActionSociale.objects.create(
            titre="Consultations gratuites", type_action="MEDICAL", date_debut=timezone.now().date(),
            compte_organisateur=organisateur,
        )
        beneficiaire = Beneficiaire.objects.create(nom="Ndiaye", prenom="Fatou")
        self.participation = ParticipationAction.objects.create(beneficiaire=beneficiaire, action=self.action)
        DetailMedical.objects.create(
            participation=self.participation, date_consultation=timezone.now().date(), type_soin="CONSULTATION",
        )
        self.sans = client_pour(creer_compte(est_admin=True, permissions=["GERER_ACTIONS_SOCIALES"]))
        self.avec = client_pour(
            creer_compte(est_admin=True, permissions=["GERER_ACTIONS_SOCIALES", "DONNEES_MEDICALES"])
        )

    def participation_vue_par(self, client):
        detail = client.get(f"/api/admin/action-sociale/{self.action.id_action}/").data
        return detail["beneficiaires"][0]

    def test_masquees_sans_permission(self):
        vue = self.participation_vue_par(self.sans)
        self.assertIsNone(vue["detail_medical"])
        self.assertTrue(vue["detail_medical_masque"])

    def test_visibles_avec_permission(self):
        vue = self.participation_vue_par(self.avec)
        self.assertIsNotNone(vue["detail_medical"])
        self.assertFalse(vue["detail_medical_masque"])

    def test_saisie_du_suivi_medical(self):
        url = (
            f"/api/admin/action-sociale/{self.action.id_action}/participation/"
            f"{self.participation.id_participation}/suivi-medical/"
        )
        self.assertEqual(self.sans.patch(url, {"a_ete_visite": True}, format="json").status_code, 403)
        self.assertEqual(self.avec.patch(url, {"a_ete_visite": True}, format="json").status_code, 200)

    def test_ajout_beneficiaire_medical_refuse_sans_rien_creer(self):
        avant = ParticipationAction.objects.count(), Beneficiaire.objects.count()
        reponse = self.sans.post(
            f"/api/admin/action-sociale/{self.action.id_action}/beneficiaire/",
            {"nom": "Sow", "prenom": "Moussa", "type_soin": "CONSULTATION"},
            format="json",
        )
        self.assertEqual(reponse.status_code, 403)
        self.assertEqual((ParticipationAction.objects.count(), Beneficiaire.objects.count()), avant)

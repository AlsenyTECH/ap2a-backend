"""Permissions granulaires des admins et contrôle d'accès aux objets (IDOR)."""

from django.test import TestCase
from django.utils import timezone

from adhesion.models import PresenceCohorte

from .outils import (
    client_pour, creer_carte, creer_cohorte, creer_compte, creer_controleur,
    creer_evenement, creer_membre, creer_participant,
)


class PermissionsGranulairesTests(TestCase):

    def setUp(self):
        self.membre = creer_membre()
        self.carte = creer_carte(self.membre)
        self.super_admin = client_pour(creer_compte(est_super_admin=True))
        self.admin_evenements = client_pour(creer_compte(est_admin=True, permissions=["GERER_EVENEMENTS"]))
        self.admin_membres = client_pour(creer_compte(est_admin=True, permissions=["GERER_MEMBRES"]))
        self.simple_membre = client_pour(creer_membre().compte)

    def test_routes_reservees_a_leur_module(self):
        routes = [
            ("get", f"/api/admin/membre/{self.membre.id_membre}/", "GERER_MEMBRES"),
            ("patch", f"/api/admin/membre/{self.membre.id_membre}/modifier/", "GERER_MEMBRES"),
            ("post", f"/api/admin/carte/{self.carte.id_carte}/bloquer/", "GERER_MEMBRES"),
            ("get", "/api/admin/controleurs/", "GERER_CONTROLEURS"),
            ("get", "/api/admin/journal/", "VOIR_RAPPORTS"),
            ("get", "/api/admin/statistiques/", "VOIR_RAPPORTS"),
            ("get", "/api/admin/rapports/export/", "VOIR_RAPPORTS"),
            ("post", "/api/admin/formation/", "GERER_FORMATIONS"),
        ]
        for methode, url, _code in routes:
            with self.subTest(url=url):
                reponse = getattr(self.admin_evenements, methode)(url, {}, format="json")
                self.assertEqual(reponse.status_code, 403)

    def test_admin_avec_la_bonne_permission(self):
        self.assertEqual(self.admin_membres.get(f"/api/admin/membre/{self.membre.id_membre}/").status_code, 200)
        self.assertEqual(self.admin_evenements.get("/api/admin/evenements/historique/").status_code, 200)
        self.assertEqual(self.admin_membres.get("/api/admin/evenements/historique/").status_code, 403)

    def test_super_admin_passe_partout(self):
        self.assertEqual(self.super_admin.get(f"/api/admin/membre/{self.membre.id_membre}/").status_code, 200)
        self.assertEqual(self.super_admin.get("/api/admin/journal/").status_code, 200)

    def test_liste_des_membres_partagee_entre_modules(self):
        self.assertEqual(self.admin_evenements.get("/api/admin/membres/").status_code, 200)
        self.assertEqual(self.admin_membres.get("/api/admin/membres/").status_code, 200)
        sans_module = client_pour(creer_compte(est_admin=True, permissions=["GERER_SUIVI"]))
        self.assertEqual(sans_module.get("/api/admin/membres/").status_code, 403)

    def test_non_admin_refuse(self):
        self.assertEqual(self.simple_membre.get("/api/admin/membres/").status_code, 403)
        self.assertEqual(self.simple_membre.get("/api/admin/journal/").status_code, 403)

    def test_admin_destitue_perd_ses_permissions_restantes(self):
        compte = creer_compte(est_admin=True, permissions=["GERER_GOUVERNANCE", "GERER_MEMBRES"])
        compte.est_admin = False
        compte.save()
        client = client_pour(compte)
        self.assertEqual(client.get("/api/admin/membres/").status_code, 403)
        reponse = client.post(
            "/api/gouvernance/comptes-rendus/",
            {"titre": "x", "date_reunion": "2026-01-01", "contenu": "y"},
            format="json",
        )
        self.assertEqual(reponse.status_code, 403)


class CertificatTests(TestCase):

    def setUp(self):
        self.cohorte, self.seances = creer_cohorte(nombre_seances=1)
        self.titulaire = creer_membre()
        self.participant = creer_participant(self.cohorte, membre=self.titulaire)
        self.url = f"/api/cohorte/{self.cohorte.id_cohorte}/certificat/{self.participant.id_participant}/"
        PresenceCohorte.objects.create(
            participant=self.participant, seance_cohorte=self.seances[0], heure_arrivee=timezone.now(),
            methode_scan="QR", controleur_scan=creer_controleur(),
        )

    def test_titulaire(self):
        reponse = client_pour(self.titulaire.compte).get(self.url)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "application/pdf")

    def test_autre_membre_refuse(self):
        """IDOR : faire varier les identifiants dans l'URL ne donne rien."""
        intrus = client_pour(creer_membre().compte)
        self.assertEqual(intrus.get(self.url).status_code, 404)

    def test_admin_d_un_autre_module_refuse(self):
        admin = client_pour(creer_compte(est_admin=True, permissions=["GERER_EVENEMENTS"]))
        self.assertEqual(admin.get(self.url).status_code, 404)

    def test_gestionnaire_des_formations(self):
        admin = client_pour(creer_compte(est_admin=True, permissions=["GERER_FORMATIONS"]))
        self.assertEqual(admin.get(self.url).status_code, 200)

    def test_participant_non_inscrit_a_la_cohorte(self):
        autre_cohorte, _ = creer_cohorte(nombre_seances=1)
        url = f"/api/cohorte/{autre_cohorte.id_cohorte}/certificat/{self.participant.id_participant}/"
        self.assertEqual(client_pour(self.titulaire.compte).get(url).status_code, 404)


class ControleurTests(TestCase):

    def test_reinitialisation_revoque_la_session(self):
        organisateur = creer_compte(est_super_admin=True)
        evenement, _ = creer_evenement(organisateur)
        controleur = creer_controleur(evenement=evenement)
        client_controleur = client_pour(controleur.compte)
        reponse = client_pour(organisateur).post(
            f"/api/admin/controleur/{controleur.id_controleur}/reinitialiser-mot-de-passe/"
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(client_controleur.get("/api/mon-profil/").status_code, 401)

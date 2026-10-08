"""Connexion, jetons de session, déconnexion, liens de téléchargement."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from adhesion.models import Jeton, TentativeConnexion

from .outils import MOT_DE_PASSE, client_pour, creer_compte, creer_membre


class ConnexionTests(TestCase):

    def setUp(self):
        self.compte = creer_compte()
        self.client = APIClient()

    def connexion(self, identifiant=None, mot_de_passe=MOT_DE_PASSE):
        return self.client.post(
            "/api/login/",
            {"email": identifiant or self.compte.email, "mot_de_passe": mot_de_passe},
            format="json",
        )

    def test_connexion_par_email_et_numero_adherent(self):
        self.assertEqual(self.connexion().status_code, 200)
        membre = creer_membre(self.compte)
        self.assertEqual(self.connexion(membre.numero_adherent).status_code, 200)

    def test_seule_l_empreinte_du_jeton_est_stockee(self):
        cle = self.connexion().data["jeton"]
        jeton = Jeton.objects.get(compte=self.compte)
        self.assertNotEqual(jeton.empreinte, cle)
        self.assertEqual(jeton.empreinte, Jeton.empreinte_de(cle))
        self.assertGreater(jeton.date_expiration, timezone.now())

    def test_reconnexion_invalide_l_ancien_jeton(self):
        ancien = self.connexion().data["jeton"]
        self.connexion()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {ancien}")
        self.assertEqual(client.get("/api/mon-profil/").status_code, 401)

    def test_mauvais_mot_de_passe(self):
        reponse = self.connexion(mot_de_passe="faux")
        self.assertEqual(reponse.status_code, 401)
        self.assertEqual(TentativeConnexion.objects.count(), 1)

    @override_settings(CONNEXION_MAX_ECHECS_IDENTIFIANT=3)
    def test_blocage_apres_trop_d_echecs_meme_avec_le_bon_mot_de_passe(self):
        for _ in range(3):
            self.assertEqual(self.connexion(mot_de_passe="faux").status_code, 401)
        reponse = self.connexion()
        self.assertEqual(reponse.status_code, 429)
        self.assertEqual(reponse.data["code"], "TROP_DE_TENTATIVES")
        self.assertGreater(int(reponse["Retry-After"]), 0)

    @override_settings(CONNEXION_MAX_ECHECS_IDENTIFIANT=3, CONNEXION_FENETRE_MINUTES=15)
    def test_deblocage_a_la_fin_de_la_fenetre(self):
        for _ in range(3):
            self.connexion(mot_de_passe="faux")
        TentativeConnexion.objects.update(date_tentative=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.connexion().status_code, 200)

    @override_settings(CONNEXION_MAX_ECHECS_IDENTIFIANT=3)
    def test_identifiant_inconnu_bloque_pareil(self):
        """Le blocage ne doit pas révéler si un compte existe."""
        for _ in range(3):
            self.assertEqual(self.connexion("inconnu@exemple.org", "faux").status_code, 401)
        self.assertEqual(self.connexion("inconnu@exemple.org", "faux").status_code, 429)

    @override_settings(CONNEXION_MAX_ECHECS_IDENTIFIANT=3)
    def test_succes_remet_le_compteur_a_zero(self):
        for _ in range(2):
            self.connexion(mot_de_passe="faux")
        self.assertEqual(self.connexion().status_code, 200)
        self.assertEqual(TentativeConnexion.objects.filter(identifiant=self.compte.email).count(), 0)

    @override_settings(CONNEXION_MAX_ECHECS_IDENTIFIANT=100, CONNEXION_MAX_ECHECS_IP=4)
    def test_blocage_par_ip(self):
        for i in range(4):
            self.connexion(f"essai{i}@exemple.org", "faux")
        self.assertEqual(self.connexion().status_code, 429)

    def test_identifiant_inconnu_verifie_quand_meme_un_hash(self):
        """Temps de réponse égalisé : un PBKDF2 est calculé même sans compte."""
        with mock.patch("adhesion.views.check_password", return_value=False) as verif:
            self.connexion("inconnu@exemple.org", "faux")
        verif.assert_called_once()


class JetonTests(TestCase):

    def setUp(self):
        self.compte = creer_compte()
        self.client = client_pour(self.compte)

    def test_jeton_valide(self):
        self.assertEqual(self.client.get("/api/mon-profil/").status_code, 200)

    def test_requete_anonyme_refusee_en_401(self):
        self.assertEqual(APIClient().get("/api/mon-profil/").status_code, 401)

    def test_jeton_expire(self):
        Jeton.objects.filter(compte=self.compte).update(date_expiration=timezone.now() - timedelta(seconds=1))
        reponse = self.client.get("/api/mon-profil/")
        self.assertEqual(reponse.status_code, 401)
        self.assertFalse(Jeton.objects.filter(compte=self.compte).exists())

    def test_deconnexion_revoque_le_jeton(self):
        self.assertEqual(self.client.post("/api/logout/").status_code, 204)
        self.assertEqual(self.client.get("/api/mon-profil/").status_code, 401)

    def test_compte_suspendu(self):
        self.compte.statut_compte = "SUSPENDU"
        self.compte.save()
        self.assertEqual(self.client.get("/api/mon-profil/").status_code, 401)

    def test_jeton_en_parametre_d_url_n_est_plus_accepte(self):
        cle = Jeton.ouvrir_session(self.compte)
        self.assertEqual(APIClient().get(f"/api/mon-profil/?jeton={cle}").status_code, 401)


class ChangementMotDePasseTests(TestCase):

    def setUp(self):
        self.compte = creer_compte()
        self.client = client_pour(self.compte)

    def changer(self, ancien, nouveau):
        return self.client.post(
            "/api/changer-mot-de-passe/",
            {"ancien_mot_de_passe": ancien, "nouveau_mot_de_passe": nouveau},
            format="json",
        )

    def test_ancien_mot_de_passe_faux_ne_deconnecte_pas(self):
        self.assertEqual(self.changer("faux", "Encore-un-autre-secret-9").status_code, 400)
        self.assertEqual(self.client.get("/api/mon-profil/").status_code, 200)

    def test_mot_de_passe_faible_refuse(self):
        for faible in ("12345678", "password", "court"):
            self.assertEqual(self.changer(MOT_DE_PASSE, faible).status_code, 400)

    def test_changement(self):
        self.assertEqual(self.changer(MOT_DE_PASSE, "Encore-un-autre-secret-9").status_code, 200)
        self.compte.refresh_from_db()
        self.assertTrue(self.compte.verifier_mot_de_passe("Encore-un-autre-secret-9"))


class LienTelechargementTests(TestCase):

    def setUp(self):
        self.admin = creer_compte(est_admin=True, permissions=["VOIR_RAPPORTS"])
        self.client = client_pour(self.admin)

    def lien(self, chemin):
        return self.client.post("/api/telechargement/lien/", {"chemin": chemin}, format="json")

    def test_lien_pour_un_export(self):
        reponse = self.lien("/admin/rapports/export/")
        self.assertEqual(reponse.status_code, 200)
        valeur = reponse.data["telechargement"]
        export = APIClient().get(
            "/api/admin/rapports/export/", {"telechargement": valeur, "type": "journal", "format": "excel"}
        )
        self.assertEqual(export.status_code, 200)

    def test_route_hors_liste_refusee(self):
        self.assertEqual(self.lien("/admin/membres/").status_code, 400)
        self.assertEqual(self.lien("/../admin/").status_code, 400)

    def test_lien_lie_a_sa_route(self):
        valeur = self.lien("/admin/rapports/export/").data["telechargement"]
        self.assertEqual(APIClient().get("/api/admin/membres/", {"telechargement": valeur}).status_code, 401)
        self.assertEqual(APIClient().get("/api/cohorte/1/badges/pdf/", {"telechargement": valeur}).status_code, 401)

    def test_lien_falsifie_ou_expire(self):
        valeur = self.lien("/admin/rapports/export/").data["telechargement"]
        self.assertEqual(
            APIClient().get("/api/admin/rapports/export/", {"telechargement": valeur + "x"}).status_code, 401
        )
        with self.settings(LIEN_TELECHARGEMENT_DUREE_SECONDES=-1):
            self.assertEqual(
                APIClient().get("/api/admin/rapports/export/", {"telechargement": valeur}).status_code, 401
            )

    def test_le_lien_n_accorde_pas_de_permission(self):
        membre = creer_compte()
        valeur = client_pour(membre).post(
            "/api/telechargement/lien/", {"chemin": "/admin/rapports/export/"}, format="json"
        ).data["telechargement"]
        self.assertEqual(
            APIClient().get("/api/admin/rapports/export/", {"telechargement": valeur}).status_code, 403
        )

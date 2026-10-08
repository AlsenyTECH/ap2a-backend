"""Vérification des cartes et tickets de scan à usage unique."""

from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from adhesion.models import Participe, PresenceCohorte, TicketScanConsomme
from adhesion.tickets import MODE_SCAN, TYPE_MEMBRE, emettre_ticket_scan
from adhesion.utils import (
    DOMAINE_BADGE, DOMAINE_CARTE, construire_contenu_carte, construire_contenu_rotatif,
)

from .outils import (
    client_pour, creer_carte, creer_cohorte, creer_compte, creer_controleur,
    creer_evenement, creer_membre, creer_participant,
)


def url_statique(contenu):
    uuid, version, signature = contenu.split(".")
    return f"/api/verifier/{uuid}/{version}/{signature}/"


def url_rotatif(contenu):
    uuid, version, fenetre, signature = contenu.split(".")
    return f"/api/verifier-rotatif/{uuid}/{version}/{fenetre}/{signature}/"


@override_settings(HMAC_ACCEPTER_SIGNATURES_HERITEES=False)
class VerificationCarteTests(TestCase):

    def setUp(self):
        self.membre = creer_membre()
        self.carte_qr = creer_carte(self.membre, "QR")
        self.carte_nfc = creer_carte(self.membre, "NFC")
        self.controleur = creer_controleur()
        self.client = client_pour(self.controleur.compte)

    def test_carte_nfc_statique(self):
        reponse = self.client.get(url_statique(construire_contenu_carte(str(self.carte_nfc.uuid))))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data["id_membre"], self.membre.id_membre)
        self.assertIn("ticket_scan", reponse.data)

    def test_carte_virtuelle_refusee_en_statique(self):
        """Une capture d'un contenu statique de carte virtuelle ne vaut rien."""
        reponse = self.client.get(url_statique(construire_contenu_carte(str(self.carte_qr.uuid))))
        self.assertEqual(reponse.status_code, 404)
        self.assertNotIn("ticket_scan", reponse.data)

    def test_carte_virtuelle_rotative(self):
        reponse = self.client.get(url_rotatif(construire_contenu_rotatif(str(self.carte_qr.uuid))))
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("ticket_scan", reponse.data)

    def test_rotatif_refuse_pour_une_carte_nfc(self):
        reponse = self.client.get(url_rotatif(construire_contenu_rotatif(str(self.carte_nfc.uuid))))
        self.assertEqual(reponse.status_code, 404)

    def test_signature_invalide_ne_deconnecte_pas(self):
        contenu = construire_contenu_carte(str(self.carte_nfc.uuid))
        reponse = self.client.get(url_statique(contenu[:-4] + "0000"))
        self.assertEqual(reponse.status_code, 403)

    def test_badge_de_participant_refuse_comme_carte(self):
        contenu = construire_contenu_carte(str(self.carte_nfc.uuid), DOMAINE_BADGE)
        self.assertEqual(self.client.get(url_statique(contenu)).status_code, 403)

    def test_carte_bloquee(self):
        self.carte_nfc.statut_carte = "BLOQUEE"
        self.carte_nfc.save()
        reponse = self.client.get(url_statique(construire_contenu_carte(str(self.carte_nfc.uuid))))
        self.assertEqual(reponse.status_code, 403)

    def test_reserve_aux_controleurs(self):
        client_membre = client_pour(self.membre.compte)
        reponse = client_membre.get(url_statique(construire_contenu_carte(str(self.carte_nfc.uuid))))
        self.assertEqual(reponse.status_code, 403)

    def test_profil_n_expose_plus_de_contenu_statique(self):
        reponse = client_pour(self.membre.compte).get("/api/membre/moi/")
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn("contenu_carte", reponse.data["carte_qr"])
        qr = client_pour(self.membre.compte).get("/api/membre/qr-actuel/")
        self.assertEqual(len(qr.data["contenu_carte"].split(".")), 4)


@override_settings(HMAC_ACCEPTER_SIGNATURES_HERITEES=False)
class ConfirmerEntreeTests(TestCase):

    def setUp(self):
        self.membre = creer_membre()
        self.carte = creer_carte(self.membre, "NFC")
        organisateur = creer_compte(est_super_admin=True)
        self.evenement, self.seances = creer_evenement(organisateur)
        self.controleur = creer_controleur(evenement=self.evenement)
        self.client = client_pour(self.controleur.compte)

    def ticket(self, client=None):
        reponse = (client or self.client).get(url_statique(construire_contenu_carte(str(self.carte.uuid))))
        return reponse.data["ticket_scan"]

    def confirmer(self, ticket, seance=None, methode="NFC", client=None, **extra):
        return (client or self.client).post(
            "/api/confirmer-entree/",
            {"ticket_scan": ticket, "id_seance": (seance or self.seances[0]).id_seance, "methode_scan": methode, **extra},
            format="json",
        )

    def test_confirmation_avec_ticket(self):
        reponse = self.confirmer(self.ticket())
        self.assertEqual(reponse.status_code, 201)
        self.assertTrue(Participe.objects.filter(membre=self.membre, seance=self.seances[0]).exists())

    def test_sans_ticket_refuse(self):
        reponse = self.client.post(
            "/api/confirmer-entree/",
            {"id_membre": self.membre.id_membre, "id_seance": self.seances[0].id_seance, "methode_scan": "QR"},
            format="json",
        )
        self.assertEqual(reponse.status_code, 403)
        self.assertEqual(reponse.data["code"], "TICKET_SCAN_INVALIDE")
        self.assertFalse(Participe.objects.exists())

    def test_ticket_a_usage_unique(self):
        ticket = self.ticket()
        self.assertEqual(self.confirmer(ticket, self.seances[0]).status_code, 201)
        reponse = self.confirmer(ticket, self.seances[1])
        self.assertEqual(reponse.status_code, 409)
        self.assertEqual(reponse.data["code"], "TICKET_SCAN_INVALIDE")
        self.assertEqual(Participe.objects.count(), 1)

    def test_id_membre_du_corps_ignore(self):
        """Le membre vient du ticket signé, pas d'un champ libre."""
        autre = creer_membre()
        self.confirmer(self.ticket(), id_membre=autre.id_membre)
        self.assertTrue(Participe.objects.filter(membre=self.membre).exists())
        self.assertFalse(Participe.objects.filter(membre=autre).exists())

    def test_ticket_falsifie(self):
        ticket = self.ticket()
        self.assertEqual(self.confirmer(ticket[:-2] + "xx").status_code, 403)

    def test_ticket_d_un_autre_controleur(self):
        autre = creer_controleur(evenement=self.evenement)
        ticket = self.ticket(client_pour(autre.compte))
        self.assertEqual(self.confirmer(ticket).status_code, 403)

    def test_ticket_expire(self):
        ticket = self.ticket()
        with self.settings(TICKET_SCAN_DUREE_SECONDES=-1):
            self.assertEqual(self.confirmer(ticket).status_code, 403)

    def test_ticket_de_scan_inutilisable_en_manuel(self):
        self.assertEqual(self.confirmer(self.ticket(), methode="MANUEL").status_code, 403)

    def test_parcours_manuel(self):
        recherche = self.client.get(f"/api/verifier-manuel/{self.membre.numero_adherent}/")
        self.assertEqual(recherche.status_code, 200)
        reponse = self.confirmer(recherche.data["ticket_scan"], methode="MANUEL")
        self.assertEqual(reponse.status_code, 201)
        # Et un ticket manuel ne passe pas pour un scan.
        recherche = self.client.get(f"/api/verifier-manuel/{self.membre.numero_adherent}/")
        self.assertEqual(self.confirmer(recherche.data["ticket_scan"], self.seances[1], "QR").status_code, 403)

    def test_echec_d_enregistrement_ne_brule_pas_le_ticket(self):
        Participe.objects.create(
            membre=self.membre, seance=self.seances[0], heure_arrivee=timezone.now(),
            methode_scan="QR", controleur_scan=self.controleur,
        )
        ticket = self.ticket()
        reponse = self.confirmer(ticket, self.seances[0])
        self.assertEqual(reponse.status_code, 409)
        self.assertNotIn("code", reponse.data)
        self.assertEqual(TicketScanConsomme.objects.count(), 0)
        self.assertEqual(self.confirmer(ticket, self.seances[1]).status_code, 201)

    def test_controleur_non_assigne(self):
        ailleurs = creer_controleur()
        client = client_pour(ailleurs.compte)
        self.assertEqual(self.confirmer(self.ticket(client), client=client).status_code, 403)

    def test_ticket_pour_un_membre_inexistant(self):
        ticket = emettre_ticket_scan(TYPE_MEMBRE, 999_999, self.controleur.compte, MODE_SCAN)
        self.assertEqual(self.confirmer(ticket).status_code, 404)

    @override_settings(TICKET_SCAN_OBLIGATOIRE=False)
    def test_mode_de_transition_sans_ticket(self):
        with mock.patch("adhesion.views.logger") as journal:
            reponse = self.client.post(
                "/api/confirmer-entree/",
                {"id_membre": self.membre.id_membre, "id_seance": self.seances[0].id_seance, "methode_scan": "QR"},
                format="json",
            )
        self.assertEqual(reponse.status_code, 201)
        journal.warning.assert_called_once()


@override_settings(HMAC_ACCEPTER_SIGNATURES_HERITEES=False)
class PresenceCohorteTests(TestCase):

    def setUp(self):
        self.cohorte, self.seances = creer_cohorte()
        self.participant = creer_participant(self.cohorte)
        self.controleur = creer_controleur()
        self.client = client_pour(self.controleur.compte)

    def verifier_badge(self, domaine=DOMAINE_BADGE):
        uuid, version, signature = construire_contenu_carte(str(self.participant.uuid), domaine).split(".")
        return self.client.get(f"/api/verifier-participant/{uuid}/{version}/{signature}/")

    def confirmer(self, client=None, **corps):
        corps.setdefault("id_seance_cohorte", self.seances[0].id_seance_cohorte)
        corps.setdefault("methode_scan", "QR")
        return (client or self.client).post("/api/confirmer-presence-cohorte/", corps, format="json")

    def test_badge_et_presence(self):
        verification = self.verifier_badge()
        self.assertEqual(verification.status_code, 200)
        self.assertEqual(self.confirmer(ticket_scan=verification.data["ticket_scan"]).status_code, 201)
        self.assertEqual(PresenceCohorte.objects.count(), 1)

    def test_signature_de_carte_refusee_pour_un_badge(self):
        self.assertEqual(self.verifier_badge(DOMAINE_CARTE).status_code, 403)

    def test_sans_ticket_refuse(self):
        reponse = self.confirmer(id_participant=self.participant.id_participant)
        self.assertEqual(reponse.status_code, 403)

    def test_ticket_de_membre_refuse_pour_un_participant(self):
        ticket = emettre_ticket_scan(TYPE_MEMBRE, self.participant.id_participant, self.controleur.compte, MODE_SCAN)
        self.assertEqual(self.confirmer(ticket_scan=ticket).status_code, 403)

    def test_saisie_manuelle_reservee_aux_gestionnaires(self):
        corps = {"id_participant": self.participant.id_participant, "methode_scan": "MANUEL"}
        self.assertEqual(self.confirmer(**corps).status_code, 403)
        gestionnaire = client_pour(creer_compte(est_admin=True, permissions=["GERER_FORMATIONS"]))
        self.assertEqual(self.confirmer(client=gestionnaire, **corps).status_code, 201)

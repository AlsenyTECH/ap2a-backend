"""Signatures HMAC des cartes, badges et QR rotatifs (adhesion/utils.py)."""

import hashlib
import hmac
from unittest import mock

from django.test import SimpleTestCase, override_settings

from adhesion import utils
from adhesion.utils import (
    DOMAINE_BADGE, DOMAINE_CARTE, construire_contenu_carte, construire_contenu_rotatif,
    decomposer_contenu_carte, generer_mot_de_passe_temporaire, generer_signature,
    generer_signature_temporelle, verifier_signature, verifier_signature_temporelle,
)

UUID = "3f2b8c1e-9d4a-4e6b-8f0a-1c2d3e4f5a6b"
CLES = {1: "a" * 64, 2: "b" * 64}


def signature_heritee(uuid, cle=CLES[1]):
    """Ancien format, sans étiquette de domaine : HMAC(K, uuid)."""
    return hmac.new(cle.encode(), uuid.encode(), hashlib.sha256).hexdigest()


@override_settings(HMAC_CLES=CLES, HMAC_VERSION_ACTIVE=1, HMAC_ACCEPTER_SIGNATURES_HERITEES=False)
class SignatureStatiqueTests(SimpleTestCase):

    def test_aller_retour(self):
        for domaine in (DOMAINE_CARTE, DOMAINE_BADGE):
            signature, version = generer_signature(UUID, domaine)
            self.assertEqual(version, 1)
            self.assertTrue(verifier_signature(UUID, signature, version, domaine))

    def test_signature_inclut_le_domaine(self):
        signature, _ = generer_signature(UUID, DOMAINE_CARTE)
        attendue = hmac.new(CLES[1].encode(), f"AP2A|carte|{UUID}".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signature, attendue)

    def test_separation_de_domaine(self):
        signature_badge, version = generer_signature(UUID, DOMAINE_BADGE)
        self.assertFalse(verifier_signature(UUID, signature_badge, version, DOMAINE_CARTE))
        signature_carte, version = generer_signature(UUID, DOMAINE_CARTE)
        self.assertFalse(verifier_signature(UUID, signature_carte, version, DOMAINE_BADGE))

    def test_signature_alteree_refusee(self):
        signature, version = generer_signature(UUID)
        alteree = ("0" if signature[0] != "0" else "1") + signature[1:]
        self.assertFalse(verifier_signature(UUID, alteree, version))

    def test_autre_uuid_refuse(self):
        signature, version = generer_signature(UUID)
        self.assertFalse(verifier_signature(UUID.replace("3f", "4f"), signature, version))

    def test_version_inconnue_ou_revoquee(self):
        signature, _ = generer_signature(UUID)
        self.assertFalse(verifier_signature(UUID, signature, 99))
        # Signée avec la clé 1 mais annoncée en version 2 : la clé 2 ne la valide pas.
        self.assertFalse(verifier_signature(UUID, signature, 2))

    def test_rotation_de_cle(self):
        signature_v1, _ = generer_signature(UUID)
        with self.settings(HMAC_VERSION_ACTIVE=2):
            signature_v2, version = generer_signature(UUID)
            self.assertEqual(version, 2)
            self.assertNotEqual(signature_v1, signature_v2)
            # L'ancienne carte reste valide tant que la clé 1 est au trousseau...
            self.assertTrue(verifier_signature(UUID, signature_v1, 1))
        # ... et ne l'est plus une fois la clé retirée.
        with self.settings(HMAC_CLES={2: CLES[2]}):
            self.assertFalse(verifier_signature(UUID, signature_v1, 1))

    def test_signature_heritee_refusee_par_defaut_ici(self):
        self.assertFalse(verifier_signature(UUID, signature_heritee(UUID), 1))

    def test_signature_heritee_acceptee_en_transition(self):
        with self.settings(HMAC_ACCEPTER_SIGNATURES_HERITEES=True):
            self.assertTrue(verifier_signature(UUID, signature_heritee(UUID), 1, DOMAINE_CARTE))
            self.assertTrue(verifier_signature(UUID, signature_heritee(UUID), 1, DOMAINE_BADGE))

    def test_contenu_carte(self):
        contenu = construire_contenu_carte(UUID)
        uuid, signature, version = decomposer_contenu_carte(contenu)
        self.assertEqual(uuid, UUID)
        self.assertTrue(verifier_signature(uuid, signature, version))
        self.assertIsNone(decomposer_contenu_carte("pas.un-contenu"))
        self.assertIsNone(decomposer_contenu_carte(f"{UUID}.x.{signature}"))


@override_settings(HMAC_CLES=CLES, HMAC_VERSION_ACTIVE=1, HMAC_ACCEPTER_SIGNATURES_HERITEES=True)
class SignatureRotativeTests(SimpleTestCase):

    def _a_l_instant(self, secondes):
        return mock.patch.object(utils.time, "time", return_value=secondes)

    def test_fenetres_acceptees(self):
        with self._a_l_instant(1_000_000):
            fenetre = utils.fenetre_temps_actuelle()
            for f in (fenetre, fenetre - 1):
                signature, version = generer_signature_temporelle(UUID, f)
                self.assertTrue(verifier_signature_temporelle(UUID, f, version, signature))

    def test_fenetres_refusees(self):
        with self._a_l_instant(1_000_000):
            fenetre = utils.fenetre_temps_actuelle()
            for f in (fenetre - 2, fenetre + 1):
                signature, version = generer_signature_temporelle(UUID, f)
                self.assertFalse(verifier_signature_temporelle(UUID, f, version, signature))

    def test_qr_expire_apres_vingt_secondes(self):
        with self._a_l_instant(1_000_000):
            contenu = construire_contenu_rotatif(UUID)
        uuid, version, fenetre, signature = contenu.split(".")
        with self._a_l_instant(1_000_000 + 25):
            self.assertFalse(verifier_signature_temporelle(uuid, int(fenetre), int(version), signature))

    def test_fenetre_falsifiee(self):
        with self._a_l_instant(1_000_000):
            fenetre = utils.fenetre_temps_actuelle()
            signature, version = generer_signature_temporelle(UUID, fenetre - 1)
            self.assertFalse(verifier_signature_temporelle(UUID, fenetre, version, signature))

    def test_signature_statique_inutilisable_comme_rotative(self):
        """Le domaine "rotatif" empêche de rejouer une signature de carte."""
        with self._a_l_instant(1_000_000):
            fenetre = utils.fenetre_temps_actuelle()
            signature, version = generer_signature(UUID)
            self.assertFalse(verifier_signature_temporelle(UUID, fenetre, version, signature))
            self.assertFalse(verifier_signature_temporelle(UUID, fenetre, version, signature_heritee(UUID)))


class MotDePasseTemporaireTests(SimpleTestCase):

    def test_format_et_alea(self):
        mots = {generer_mot_de_passe_temporaire() for _ in range(200)}
        self.assertEqual(len(mots), 200)
        for mot in mots:
            self.assertTrue(mot.startswith("AP2A-"))
            self.assertEqual(len(mot), 17)
            self.assertFalse(set(mot[5:]) & set("0O1lI"))

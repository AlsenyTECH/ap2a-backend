"""Cibles : doublons, fusion, appartenances, historique, import Excel, permissions."""

import io

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from adhesion.cibles import doublons_potentiels, fusionner, normaliser_telephone, normaliser_texte
from adhesion.models import (
    ActionSociale, Appartenance, Beneficiaire, Cible, JournalAudit, ParticipationAction, Zone,
)

from .outils import client_pour, creer_compte, creer_membre


def personne(**champs):
    return Cible.objects.create(**{"type_cible": "PERSONNE", "nom": "Ndiaye", "prenom": "Awa", **champs})


class NormalisationTests(SimpleTestCase):

    def test_telephone(self):
        for brut in ("+221 77 123 45 67", "00221771234567", "77 123 45 67", "221771234567", "77-123-45-67"):
            self.assertEqual(normaliser_telephone(brut), "771234567", brut)
        self.assertEqual(normaliser_telephone(None), "")

    def test_texte(self):
        self.assertEqual(normaliser_texte("  N'Diaye-SOW  "), "n diaye sow")
        self.assertEqual(normaliser_texte("Médina Gounass"), "medina gounass")


class DoublonsTests(TestCase):

    def test_meme_telephone_sous_un_autre_format(self):
        existante = personne(telephone="+221 77 123 45 67")
        doublons = doublons_potentiels({"type_cible": "PERSONNE", "nom": "Fall", "prenom": "Ali", "telephone": "771234567"})
        self.assertEqual([(c.pk, r) for c, r in doublons], [(existante.pk, ["Même téléphone"])])

    def test_meme_nom_malgre_accents_et_casse(self):
        existante = personne(nom="N'Diaye", prenom="Aïssatou")
        doublons = doublons_potentiels({"type_cible": "PERSONNE", "nom": "n diaye", "prenom": "AISSATOU"})
        self.assertEqual(doublons[0][0].pk, existante.pk)

    def test_dates_de_naissance_differentes(self):
        personne(date_naissance="1990-01-01")
        self.assertEqual(
            doublons_potentiels({"type_cible": "PERSONNE", "nom": "Ndiaye", "prenom": "Awa", "date_naissance": "1985-05-05"}),
            [],
        )

    def test_piece_identite(self):
        existante = personne(nom="Ba", prenom="Omar", numero_identification="1 234 5678 90123")
        doublons = doublons_potentiels(
            {"type_cible": "PERSONNE", "nom": "X", "prenom": "Y", "numero_identification": "1234 5678 90123"}
        )
        self.assertEqual(doublons, [])  # les espaces comptent comme séparateurs de mots
        doublons = doublons_potentiels(
            {"type_cible": "PERSONNE", "nom": "X", "prenom": "Y", "numero_identification": "1-234-5678-90123"}
        )
        self.assertEqual(doublons[0][0].pk, existante.pk)

    def test_collectif_dans_une_autre_zone(self):
        pikine = Zone.objects.get(nom="Pikine", niveau="DEPARTEMENT")
        thies = Zone.objects.get(nom="Thiès", niveau="DEPARTEMENT")
        Cible.objects.create(type_cible="ETABLISSEMENT", nom="École Thiaroye 2", zone=pikine)
        self.assertEqual(
            doublons_potentiels({"type_cible": "ETABLISSEMENT", "nom": "ecole thiaroye 2", "zone": thies.pk}), []
        )
        self.assertEqual(
            len(doublons_potentiels({"type_cible": "ETABLISSEMENT", "nom": "ecole thiaroye 2", "zone": pikine.pk})), 1
        )

    def test_types_differents(self):
        personne()
        self.assertEqual(doublons_potentiels({"type_cible": "GROUPE", "nom": "Ndiaye"}), [])


class FusionTests(TestCase):

    def test_fusion(self):
        principale = personne(telephone="771234567")
        membre = creer_membre()
        doublon = personne(prenom="Awa", date_naissance="1985-03-12", notes="vue à Kaolack", membre=membre)
        gie = Cible.objects.create(type_cible="GROUPE", nom="GIE Jappo")
        asc = Cible.objects.create(type_cible="ASC", nom="ASC Diambars")
        Appartenance.objects.create(personne=doublon, collectif=gie, role="Présidente")
        Appartenance.objects.create(personne=doublon, collectif=asc)
        Appartenance.objects.create(personne=principale, collectif=asc)

        fusionner(principale, doublon)
        principale.refresh_from_db()
        self.assertFalse(Cible.objects.filter(pk=doublon.pk).exists())
        self.assertEqual(str(principale.date_naissance), "1985-03-12")
        self.assertEqual(principale.telephone, "771234567")
        self.assertEqual(principale.membre, membre)
        self.assertIn("Kaolack", principale.notes)
        self.assertEqual(
            set(principale.appartenances.values_list("collectif__nom", flat=True)), {"GIE Jappo", "ASC Diambars"}
        )

    def test_fusion_impossible(self):
        a = personne()
        with self.assertRaises(ValueError):
            fusionner(a, a)
        with self.assertRaises(ValueError):
            fusionner(a, Cible.objects.create(type_cible="GROUPE", nom="G"))


class RepriseBeneficiairesTests(TestCase):

    def test_nouveau_beneficiaire_devient_cible(self):
        beneficiaire = Beneficiaire.objects.create(nom="Sarr", prenom="Fatou", telephone="+221 78 000 11 22")
        cible = Cible.objects.get(beneficiaire_origine=beneficiaire)
        self.assertEqual((cible.type_cible, cible.nom_complet, cible.telephone_normalise),
                         ("PERSONNE", "Fatou Sarr", "780001122"))

    def test_rattache_a_une_cible_existante_de_meme_telephone(self):
        existante = personne(telephone="780001122")
        beneficiaire = Beneficiaire.objects.create(nom="Sarr", prenom="Fatou", telephone="78 000 11 22")
        existante.refresh_from_db()
        self.assertEqual(existante.beneficiaire_origine, beneficiaire)
        self.assertEqual(Cible.objects.count(), 1)


class ApiCiblesTests(TestCase):

    def setUp(self):
        self.gestionnaire = client_pour(creer_compte(est_admin=True, permissions=["GERER_CIBLES"]))
        self.pikine = Zone.objects.get(nom="Pikine", niveau="DEPARTEMENT")

    def creer(self, client=None, **donnees):
        donnees = {"type_cible": "PERSONNE", "nom": "Ndiaye", "prenom": "Awa", **donnees}
        return (client or self.gestionnaire).post("/api/admin/cibles/", donnees, format="json")

    def test_creation_et_doublon(self):
        self.assertEqual(self.creer(telephone="771234567").status_code, 201)
        reponse = self.creer(nom="Fall", prenom="Ali", telephone="+221771234567")
        self.assertEqual(reponse.status_code, 409)
        self.assertEqual(reponse.data["code"], "DOUBLONS_POTENTIELS")
        self.assertEqual(reponse.data["doublons"][0]["raisons"], ["Même téléphone"])
        self.assertEqual(self.creer(nom="Fall", prenom="Ali", telephone="+221771234567", forcer=True).status_code, 201)

    def test_regles_par_type(self):
        self.assertEqual(self.creer(prenom="").status_code, 400)
        ecole = self.creer(type_cible="ETABLISSEMENT", nom="École Thiaroye 2", prenom="ignoré", effectif=640,
                           zone=self.pikine.id_zone)
        self.assertEqual(ecole.status_code, 201)
        self.assertEqual(ecole.data["prenom"], "")
        self.assertEqual(ecole.data["zone_chemin"], "Dakar > Pikine")
        membre = creer_membre()
        self.assertEqual(
            self.creer(type_cible="GROUPE", nom="GIE", membre=membre.id_membre).status_code, 400
        )
        reponse = self.gestionnaire.patch(
            f"/api/admin/cibles/{ecole.data['id_cible']}/", {"type_cible": "PERSONNE"}, format="json"
        )
        self.assertEqual(reponse.status_code, 400)

    def test_liste_filtres_et_pagination(self):
        commune = Zone.objects.create(nom="Thiaroye-sur-Mer", niveau="COMMUNE", parent=self.pikine)
        for i in range(30):
            personne(nom=f"Diop{i:02d}", prenom="Modou", zone=commune if i < 3 else None)
        Cible.objects.create(type_cible="ASC", nom="ASC Diambars")
        liste = self.gestionnaire.get("/api/admin/cibles/").data
        self.assertEqual((liste["total"], liste["pages"], len(liste["resultats"])), (31, 2, 25))
        self.assertEqual(self.gestionnaire.get("/api/admin/cibles/", {"type": "ASC"}).data["total"], 1)
        # Filtre par département : inclut ses communes.
        self.assertEqual(self.gestionnaire.get("/api/admin/cibles/", {"zone": self.pikine.id_zone}).data["total"], 3)
        self.assertEqual(self.gestionnaire.get("/api/admin/cibles/", {"q": "diop0 modou"}).data["total"], 10)

    def test_recherche_sans_accents(self):
        Cible.objects.create(type_cible="ETABLISSEMENT", nom="École Thiaroye 2")
        self.assertEqual(self.gestionnaire.get("/api/admin/cibles/", {"q": "ecole thiaroye"}).data["total"], 1)

    def test_recherche_par_telephone(self):
        personne(telephone="+221 77 555 66 77")
        self.assertEqual(self.gestionnaire.get("/api/admin/cibles/", {"q": "775556677"}).data["total"], 1)

    def test_appartenances(self):
        awa = personne()
        gie = Cible.objects.create(type_cible="GROUPE", nom="GIE Jappo")
        reponse = self.gestionnaire.post(
            f"/api/admin/cibles/{gie.id_cible}/appartenances/", {"id_cible": awa.id_cible, "role": "Présidente"},
            format="json",
        )
        self.assertEqual(reponse.status_code, 201)
        self.assertEqual(reponse.data["fiche"]["appartenances"][0]["cible"]["nom_complet"], "Awa Ndiaye")
        fiche_awa = self.gestionnaire.get(f"/api/admin/cibles/{awa.id_cible}/").data
        self.assertEqual(fiche_awa["appartenances"][0]["cible"]["nom_complet"], "GIE Jappo")
        doublon = self.gestionnaire.post(
            f"/api/admin/cibles/{awa.id_cible}/appartenances/", {"id_cible": gie.id_cible}, format="json"
        )
        self.assertEqual(doublon.status_code, 409)
        deux_personnes = self.gestionnaire.post(
            f"/api/admin/cibles/{awa.id_cible}/appartenances/", {"id_cible": personne(prenom="Bob").id_cible},
            format="json",
        )
        self.assertEqual(deux_personnes.status_code, 400)
        id_appartenance = reponse.data["id_appartenance"]
        self.assertEqual(self.gestionnaire.delete(f"/api/admin/appartenances/{id_appartenance}/").status_code, 204)

    def test_historique_et_suppression_protegee(self):
        beneficiaire = Beneficiaire.objects.create(nom="Sarr", prenom="Fatou")
        cible = Cible.objects.get(beneficiaire_origine=beneficiaire)
        action = ActionSociale.objects.create(
            titre="Kits ramadan", type_action="DON", date_debut=timezone.now().date(),
            compte_organisateur=creer_compte(est_super_admin=True),
        )
        ParticipationAction.objects.create(beneficiaire=beneficiaire, action=action)
        fiche = self.gestionnaire.get(f"/api/admin/cibles/{cible.id_cible}/").data
        self.assertEqual(fiche["historique"][0]["titre"], "Kits ramadan")
        self.assertEqual(fiche["nombre_actions"], 1)
        self.assertEqual(self.gestionnaire.delete(f"/api/admin/cibles/{cible.id_cible}/").status_code, 409)
        self.assertEqual(self.gestionnaire.delete(f"/api/admin/cibles/{personne(prenom='Z').id_cible}/").status_code, 204)

    def test_fusion_via_api(self):
        a, b = personne(), personne(telephone="771234567")
        reponse = self.gestionnaire.post(f"/api/admin/cibles/{a.id_cible}/fusionner/", {"id_doublon": b.id_cible})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data["telephone"], "771234567")
        self.assertTrue(JournalAudit.objects.filter(type_action="FUSION_CIBLES").exists())

    def test_permissions(self):
        lecteur = client_pour(creer_compte(est_admin=True, permissions=["GERER_ACTIONS_SOCIALES"]))
        self.assertEqual(lecteur.get("/api/admin/cibles/").status_code, 200)
        self.assertEqual(self.creer(client=lecteur).status_code, 403)
        sans = client_pour(creer_compte(est_admin=True, permissions=["GERER_EVENEMENTS"]))
        self.assertEqual(sans.get("/api/admin/cibles/").status_code, 403)
        self.assertEqual(client_pour(creer_membre().compte).get("/api/admin/cibles/").status_code, 403)
        cible = personne()
        self.assertEqual(sans.get(f"/api/admin/cibles/{cible.id_cible}/").status_code, 403)


class ImportExcelTests(TestCase):

    def setUp(self):
        self.client_api = client_pour(creer_compte(est_admin=True, permissions=["GERER_CIBLES"]))

    def importer(self, lignes):
        modele = self.client_api.get("/api/admin/cibles/modele-excel/")
        classeur = openpyxl.load_workbook(io.BytesIO(modele.content))
        feuille = classeur.active
        feuille.delete_rows(2, feuille.max_row)
        for ligne in lignes:
            feuille.append(ligne)
        tampon = io.BytesIO()
        classeur.save(tampon)
        fichier = SimpleUploadedFile("cibles.xlsx", tampon.getvalue())
        return self.client_api.post("/api/admin/cibles/importer-excel/", {"fichier": fichier}, format="multipart")

    def test_modele_reimporte_tel_quel(self):
        modele = self.client_api.get("/api/admin/cibles/modele-excel/")
        fichier = SimpleUploadedFile("modele.xlsx", modele.content)
        reponse = self.client_api.post("/api/admin/cibles/importer-excel/", {"fichier": fichier}, format="multipart")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data["erreurs"], [])
        self.assertEqual(reponse.data["crees"], 5)
        self.assertEqual(reponse.data["appartenances"], 2)
        awa = Cible.objects.get(prenom="Awa")
        self.assertEqual(awa.appartenances.get().role, "Présidente")
        self.assertEqual(str(awa.date_naissance), "1985-03-12")
        self.assertEqual(Cible.objects.get(nom="GIE Jappo").zone.niveau, "DEPARTEMENT")

    def test_doublons_et_erreurs(self):
        personne(telephone="771112233")
        reponse = self.importer([
            ["Personne", "Fall", "Ali", "M", "", "77 111 22 33", "", "", "", "", "", "", "", ""],
            ["Planète", "X", "", "", "", "", "", "", "", "", "", "", "", ""],
            ["Personne", "Diop", "", "", "", "", "", "", "", "", "", "", "", ""],
            ["Personne", "Ba", "Omar", "", "31/02/2000", "", "", "", "", "", "", "", "", ""],
            ["Groupe", "GIE Inconnu", "", "", "", "", "", "", "", "", "Atlantide", "", "", ""],
            ["Personne", "Gaye", "Ami", "F", "", "", "", "", "", "", "", "", "Collectif fantôme", ""],
        ])
        self.assertEqual(reponse.data["crees"], 1)  # seule Ami Gaye (sans rattachement)
        self.assertEqual(reponse.data["doublons"][0]["ligne"], 2)
        self.assertEqual(len(reponse.data["erreurs"]), 5)
        self.assertTrue(any("Atlantide" in e for e in reponse.data["erreurs"]))

    def test_zone_ambigue_et_chemin(self):
        # "Dakar" est à la fois une région et un département : le plus fin l'emporte.
        reponse = self.importer([["Groupe", "ASC Médina", "", "", "", "", "", "", "", "", "Dakar", "", "", ""]])
        self.assertEqual(Cible.objects.get(nom="ASC Médina").zone.niveau, "DEPARTEMENT")
        pikine = Zone.objects.get(nom="Pikine", niveau="DEPARTEMENT")
        Zone.objects.create(nom="Darou Salam", niveau="COMMUNE", parent=pikine)
        Zone.objects.create(nom="Darou Salam", niveau="COMMUNE", parent=Zone.objects.get(nom="Mbacké"))
        reponse = self.importer([
            ["Groupe", "G1", "", "", "", "", "", "", "", "", "Darou Salam", "", "", ""],
            ["Groupe", "G2", "", "", "", "", "", "", "", "", "Dakar > Pikine > Darou Salam", "", "", ""],
        ])
        self.assertEqual(reponse.data["crees"], 1)
        self.assertIn("ambiguë", reponse.data["erreurs"][0])

    def test_fichier_invalide(self):
        fichier = SimpleUploadedFile("x.xlsx", b"pas un classeur")
        reponse = self.client_api.post("/api/admin/cibles/importer-excel/", {"fichier": fichier}, format="multipart")
        self.assertEqual(reponse.status_code, 400)

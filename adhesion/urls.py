"""
adhesion/urls.py

Routes propres à l'app adhesion. Incluses depuis core/urls.py
sous le préfixe /api/.
"""

from django.urls import path
from . import views
from . import views_nouveaux_modules as vnm

urlpatterns = [
    # --- Public / membre ---
    path("login/", views.vue_connexion, name="connexion"),
    path("logout/", views.vue_deconnexion, name="deconnexion"),
    path("telechargement/lien/", views.vue_lien_telechargement, name="lien_telechargement"),
    path("changer-mot-de-passe/", views.vue_changer_mot_de_passe, name="changer_mdp"),
    path("mon-profil/", views.vue_mon_profil_compte, name="mon_profil_compte_get"),
    path("mon-profil/modifier/", views.vue_modifier_profil_compte, name="mon_profil_compte_modifier"),
    path("mon-profil/photo/", views.vue_televerser_photo, name="televerser_photo"),
    path("carte/declarer-perte/", views.vue_declarer_perte, name="declarer_perte"),
    path("membre/moi/", views.vue_mon_profil, name="mon_profil"),
    path("membre/qr-actuel/", views.vue_qr_actuel, name="qr_actuel"),
    path("membre/historique/", views.vue_mon_historique, name="mon_historique"),

    # --- Contrôleur ---
    path(
        "verifier/<uuid:uuid_carte>/<int:version>/<str:signature>/",
        views.vue_verifier,
        name="verifier",
    ),
    path(
        "verifier-rotatif/<uuid:uuid_carte>/<int:version>/<int:fenetre>/<str:signature>/",
        views.vue_verifier_rotatif,
        name="verifier_rotatif",
    ),
    path(
        "verifier-manuel/<str:numero_adherent>/",
        views.vue_verifier_manuel,
        name="verifier_manuel",
    ),
    path("confirmer-entree/", views.vue_confirmer_entree, name="confirmer_entree"),
    path("evenements/", views.vue_liste_evenements, name="liste_evenements"),
    path("evenement/<int:id_evenement>/seances/", views.vue_liste_seances, name="liste_seances"),

    # --- Admin : membres ---
    path("admin/membres/", views.vue_liste_membres, name="liste_membres"),
    path("admin/sections/", views.vue_liste_sections, name="liste_sections"),
    path("admin/membre/<int:id_membre>/", views.vue_detail_membre, name="detail_membre"),
    path("admin/membre/<int:id_membre>/modifier/", views.vue_modifier_membre, name="modifier_membre"),
    path("admin/carte/<int:id_carte>/bloquer/", views.vue_bloquer_carte, name="bloquer_carte"),
    path("admin/carte/<int:id_carte>/activer/", views.vue_activer_carte, name="activer_carte"),
    path("admin/controleurs/", views.vue_liste_controleurs, name="liste_controleurs"),
    path(
        "admin/controleur/<int:id_controleur>/",
        views.vue_modifier_controleur,
        name="modifier_controleur",
    ),
    path(
        "admin/controleur/<int:id_controleur>/supprimer/",
        views.vue_supprimer_controleur,
        name="supprimer_controleur",
    ),
    path(
        "admin/controleur/<int:id_controleur>/assigner/",
        views.vue_assigner_controleur,
        name="assigner_controleur",
    ),
    path(
        "admin/controleur/<int:id_controleur>/reinitialiser-mot-de-passe/",
        views.vue_reinitialiser_mot_de_passe_controleur,
        name="reinitialiser_mot_de_passe_controleur",
    ),
    path("controleur/historique/", views.vue_mon_historique_controleur, name="mon_historique_controleur"),
    path("admin/recherche-comptes/", views.vue_recherche_comptes, name="recherche_comptes"),
    path("admin/nommer-admin/", views.vue_nommer_admin, name="nommer_admin"),
    path("admin/liste-admins/", views.vue_liste_admins, name="liste_admins"),
    path(
        "admin/destituer-admin/<int:id_compte>/",
        views.vue_destituer_admin,
        name="destituer_admin",
    ),
    path("admin/creer-controleur/", views.vue_creer_controleur, name="creer_controleur"),
    path("admin/membre/creer/", views.vue_creer_membre_admin, name="creer_membre_admin"),

    # --- Admin : événements ---
    path("admin/evenement/", views.vue_creer_evenement, name="creer_evenement"),
    path(
        "admin/evenement/<int:id_evenement>/modifier/",
        views.vue_modifier_ou_supprimer_evenement,
        name="modifier_ou_supprimer_evenement",
    ),
    path(
        "admin/evenement/<int:id_evenement>/annuler/",
        views.vue_annuler_evenement,
        name="annuler_evenement",
    ),
    path(
        "admin/evenement/<int:id_evenement>/terminer/",
        views.vue_terminer_evenement,
        name="terminer_evenement",
    ),
    path(
        "admin/evenement/<int:id_evenement>/",
        views.vue_detail_evenement,
        name="detail_evenement",
    ),
    path(
        "admin/evenements/historique/",
        views.vue_historique_evenements,
        name="historique_evenements",
    ),
    path("admin/statistiques/", views.vue_statistiques, name="statistiques"),
    path("admin/journal/", views.vue_journal_audit, name="journal_audit"),
    path("admin/rapport-controle-acces/", views.vue_rapport_controle_acces, name="rapport_controle_acces"),
    path("admin/rapports/export/", views.vue_exporter_rapport, name="exporter_rapport"),

    # --- Formation / Cohorte (existant) ---
    path("formations/", views.vue_liste_formations, name="liste_formations"),
    path("admin/formation/", views.vue_creer_formation, name="creer_formation"),
    path("admin/formation/<int:id_formation>/", views.vue_modifier_ou_supprimer_formation, name="formation_detail"),
    path("formation/<int:id_formation>/cohortes/", views.vue_liste_cohortes, name="liste_cohortes"),
    path("admin/cohorte/", views.vue_creer_cohorte, name="creer_cohorte"),
    path("admin/cohorte/<int:id_cohorte>/statut/", views.vue_changer_statut_cohorte, name="changer_statut_cohorte"),
    path("admin/cohorte/<int:id_cohorte>/terminer/", views.vue_terminer_cohorte, name="terminer_cohorte"),
    path("admin/cohorte/<int:id_cohorte>/", views.vue_detail_cohorte, name="detail_cohorte"),
    path("admin/cohorte/<int:id_cohorte>/modifier/", views.vue_modifier_ou_supprimer_cohorte, name="modifier_supprimer_cohorte"),
    path("cohorte/<int:id_cohorte>/inscription/", views.vue_sinscrire_cohorte, name="sinscrire_cohorte"),
    path("membre/mes-cohortes/", views.vue_mes_cohortes, name="mes_cohortes"),
    path("cohorte/<int:id_cohorte>/seances/", views.vue_seances_cohorte, name="seances_cohorte"),
    path(
        "admin/cohorte/<int:id_cohorte>/participants/",
        views.vue_liste_participants_cohorte,
        name="liste_participants_cohorte",
    ),
    path(
        "admin/cohorte/<int:id_cohorte>/participant/",
        views.vue_ajouter_participant_manuel,
        name="ajouter_participant_manuel",
    ),
    path(
        "admin/inscription/<int:id_inscription>/kit/",
        views.vue_basculer_kit_distribue,
        name="basculer_kit_distribue",
    ),
    path(
        "admin/inscription/<int:id_inscription>/",
        views.vue_retirer_participant_cohorte,
        name="retirer_participant_cohorte",
    ),
    path(
        "admin/cohorte/<int:id_cohorte>/seances/",
        views.vue_ajouter_seance_cohorte,
        name="ajouter_seance_cohorte",
    ),
    path(
        "admin/seance-cohorte/<int:id_seance_cohorte>/",
        views.vue_gerer_seance_cohorte,
        name="gerer_seance_cohorte",
    ),
    path(
        "admin/cohorte/<int:id_cohorte>/importer-excel/",
        views.vue_importer_participants_excel,
        name="importer_participants_excel",
    ),
    path("admin/participant/<int:id_participant>/", views.vue_detail_participant, name="detail_participant"),
    path("admin/participant/<int:id_participant>/modifier/", views.vue_modifier_participant, name="modifier_participant"),
    path(
        "admin/participant/<int:id_participant>/photo/",
        views.vue_televerser_photo_participant,
        name="televerser_photo_participant",
    ),
    path(
        "verifier-participant/<uuid:uuid_participant>/<int:version>/<str:signature>/",
        views.vue_verifier_participant,
        name="verifier_participant",
    ),
    path(
        "confirmer-presence-cohorte/",
        views.vue_confirmer_presence_cohorte,
        name="confirmer_presence_cohorte",
    ),

    # =================================================================
    # NOUVEAUX MODULES
    # =================================================================

    # --- Permissions admin (super admin uniquement) ---
    path("admin/nommer-admin-v2/", vnm.vue_nommer_admin_avec_permissions, name="nommer_admin_v2"),
    path("admin/permissions/<int:id_compte>/", vnm.vue_detail_admin_permissions, name="detail_permissions"),
    path("admin/permissions/<int:id_compte>/modifier/", vnm.vue_modifier_permissions_admin, name="modifier_permissions"),

    # --- Actions sociales ---
    path("admin/action-sociale/", vnm.vue_creer_action_sociale, name="creer_action_sociale"),
    path("actions-sociales/", vnm.vue_liste_actions_sociales, name="liste_actions_sociales"),
    path("admin/action-sociale/<int:id_action>/", vnm.vue_detail_action_sociale, name="detail_action_sociale"),
    path("admin/action-sociale/<int:id_action>/beneficiaire/", vnm.vue_ajouter_beneficiaire, name="ajouter_beneficiaire"),
    path("admin/beneficiaire/<int:id_beneficiaire>/", vnm.vue_modifier_beneficiaire, name="modifier_beneficiaire"),
    path(
        "admin/action-sociale/<int:id_action>/participation/<int:id_participation>/",
        vnm.vue_modifier_ou_retirer_participation,
        name="modifier_ou_retirer_participation",
    ),
    path("admin/action-sociale/<int:id_action>/importer-excel/", vnm.vue_importer_beneficiaires_excel, name="importer_beneficiaires_excel"),

    # --- Confirmation événement (membre) ---
    path("membre/evenements/", vnm.vue_liste_evenements_membre, name="liste_evenements_membre"),
    path("evenement/<int:id_evenement>/confirmer/", vnm.vue_confirmer_evenement, name="confirmer_evenement"),
    path("evenement/<int:id_evenement>/annuler-confirmation/", vnm.vue_annuler_confirmation, name="annuler_confirmation"),
    path("admin/evenement/<int:id_evenement>/confirmations/", vnm.vue_confirmations_evenement, name="confirmations_evenement"),

    # --- Invités externes ---
    path("admin/evenement/<int:id_evenement>/invite/", vnm.vue_ajouter_invite_externe, name="ajouter_invite"),
    path("admin/evenement/<int:id_evenement>/importer-invites/", vnm.vue_importer_invites_excel, name="importer_invites"),

    # --- Suivi post-formation ---
    path("admin/suivi-formation/", vnm.vue_creer_suivi, name="creer_suivi"),
    path("admin/cohorte/<int:id_cohorte>/suivis/", vnm.vue_liste_suivis_cohorte, name="suivis_cohorte"),
    path("admin/participant/<int:id_participant>/suivis/", vnm.vue_historique_suivis_participant, name="suivis_participant"),

    # --- Notifications ---
    path("notifications/", vnm.vue_mes_notifications, name="mes_notifications"),
    path("notifications/<int:id_notification>/lu/", vnm.vue_marquer_notification_lue, name="notification_lue"),
    path("notifications/tout-lu/", vnm.vue_marquer_toutes_lues, name="tout_lu"),

    # --- Dashboard temps réel ---
    path("dashboard/stats/", vnm.vue_dashboard_stats, name="dashboard_stats"),

    # --- Annuaire interne ---
    path("annuaire/", vnm.vue_annuaire, name="annuaire"),

    # --- Gouvernance ---
    path("gouvernance/comptes-rendus/", vnm.vue_comptes_rendus, name="comptes_rendus"),
    path("gouvernance/comptes-rendus/<int:id_compte_rendu>/", vnm.vue_detail_compte_rendu, name="detail_compte_rendu"),

    # --- Site public (AllowAny) ---
    path("public/evenements/", vnm.vue_publique_evenements, name="public_evenements"),
    path("public/actions-sociales/", vnm.vue_publique_actions_sociales, name="public_actions_sociales"),
    path("public/impact/", vnm.vue_publique_impact, name="public_impact"),
    path("public/actualites/", vnm.vue_publique_actualites, name="public_actualites"),
    path("public/actualites/<int:id_actualite>/", vnm.vue_publique_detail_actualite, name="public_detail_actualite"),
    path("admin/actualite/", vnm.vue_creer_actualite, name="creer_actualite"),
    path("admin/actualite/<int:id_actualite>/", vnm.vue_gerer_actualite, name="gerer_actualite"),

    # --- Import membres Excel ---
    path("admin/importer-membres/", vnm.vue_importer_membres_excel, name="importer_membres"),

    # --- Configuration association ---
    path("admin/config/", vnm.vue_config_association, name="config_association"),
    path("admin/config/modifier/", vnm.vue_modifier_config, name="modifier_config"),

    # --- Certificats PDF & Vérification Publique ---
    path(
        "cohorte/<int:id_cohorte>/certificat/<int:id_participant>/",
        vnm.vue_telecharger_certificat_participant,
        name="telecharger_certificat",
    ),
    path(
        "public/verifier-certificat/<str:numero_certificat>/",
        vnm.vue_verifier_certificat_public,
        name="verifier_certificat_public",
    ),

    # --- Badges PDF (Planche & Unique) ---
    path(
        "cohorte/<int:id_cohorte>/badges/pdf/",
        vnm.vue_telecharger_badges_cohorte_lot,
        name="badges_cohorte_lot",
    ),
    path(
        "cohorte/<int:id_cohorte>/badge/<int:id_participant>/",
        vnm.vue_telecharger_badge_participant,
        name="badge_participant",
    ),

    # --- Certificats & Kits en lot ---
    path(
        "cohorte/<int:id_cohorte>/certificats/pdf-lot/",
        vnm.vue_telecharger_certificats_lot,
        name="certificats_cohorte_lot",
    ),
    path(
        "cohorte/<int:id_cohorte>/kits/valider-lot/",
        vnm.vue_valider_kits_lot,
        name="valider_kits_lot",
    ),

    # --- Actions Sociales : Dons & Suivi Médical ---
    path(
        "admin/action-sociale/<int:id_action>/participation/<int:id_participation>/basculer-don/",
        vnm.vue_basculer_don_recu,
        name="basculer_don_recu",
    ),
    path(
        "admin/action-sociale/<int:id_action>/participation/<int:id_participation>/suivi-medical/",
        vnm.vue_mettre_a_jour_suivi_medical,
        name="suivi_medical",
    ),

    # --- Acter / Programmer les statuts ---
    path(
        "admin/cohorte/<int:id_cohorte>/acter-programme/",
        vnm.vue_acter_programme_cohorte,
        name="acter_programme_cohorte",
    ),
    path(
        "admin/evenement/<int:id_evenement>/acter-programme/",
        vnm.vue_acter_programme_evenement,
        name="acter_programme_evenement",
    ),

    # --- Renvoyer identifiants membre ---
    path(
        "admin/membre/<int:id_membre>/renvoyer-identifiants/",
        vnm.vue_renvoyer_identifiants_membre,
        name="renvoyer_identifiants_membre",
    ),

    # --- Kits pédagogiques (module de paramétrage séparé des formations) ---
    path("admin/kit/", vnm.vue_creer_kit, name="creer_kit"),
    path("kits/", vnm.vue_liste_kits, name="liste_kits"),
    path("admin/kit/<int:id_kit>/", vnm.vue_detail_ou_gerer_kit, name="detail_gerer_kit"),

    # --- Distribution des kits (suivi par participant) ---
    path(
        "admin/cohorte/<int:id_cohorte>/distribution/",
        vnm.vue_distribution_cohorte,
        name="distribution_cohorte",
    ),
    path(
        "admin/kit/<int:id_kit>/participant/<int:id_participant>/distribuer/",
        vnm.vue_basculer_distribution_kit,
        name="basculer_distribution_kit",
    ),
    path("admin/kit/<int:id_kit>/distribution/", vnm.vue_distribution_kit, name="distribution_kit"),
]
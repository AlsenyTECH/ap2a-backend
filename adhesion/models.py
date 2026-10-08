"""
models.py - Système de Carte d'Adhérent (Parti Politique)

Traduction directe du MLD validé (voir mld_carte_association.sql).
Chaque classe = une table. Chaque ForeignKey = une clé étrangère,
avec on_delete choisi pour reproduire exactement les contraintes
ON DELETE définies dans le script SQL (CASCADE / SET_NULL / RESTRICT/PROTECT).

Choix d'architecture : COMPTE est un modèle "maison", pas basé sur
django.contrib.auth.AbstractUser. C'est un choix pédagogique pour
garder une correspondance 1:1 avec le MCD/MLD qu'on a conçu ensemble.
En production, il serait courant d'utiliser AbstractBaseUser à la
place pour bénéficier des mécanismes Django intégrés (sessions,
réinitialisation de mot de passe, admin Django natif, etc.).
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone


# ---------------------------------------------------------------------
# SECTION : cellule/section géographique du parti
# ---------------------------------------------------------------------
class Section(models.Model):
    id_section = models.AutoField(primary_key=True)
    nom_section = models.CharField(max_length=100)
    ville = models.CharField(max_length=100)

    class Meta:
        db_table = "SECTION"

    def __str__(self):
        return f"{self.nom_section} ({self.ville})"


# ---------------------------------------------------------------------
# COMPTE : identité + authentification, commune à tous les rôles
# ---------------------------------------------------------------------
class Compte(models.Model):
    STATUT_CHOICES = [
        ("ACTIF", "Actif"),
        ("SUSPENDU", "Suspendu"),
    ]

    id_compte = models.AutoField(primary_key=True)
    email = models.EmailField(max_length=150, unique=True)
    mot_de_passe_hash = models.CharField(max_length=255)
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    date_creation = models.DateTimeField(auto_now_add=True)
    statut_compte = models.CharField(
        max_length=20, choices=STATUT_CHOICES, default="ACTIF"
    )
    est_admin = models.BooleanField(default=False)
    # Le super admin (fondateur) a tous les droits et ne peut pas
    # être destitué. Il est le seul à pouvoir désigner/destituer
    # d'autres admins et leur attribuer des permissions granulaires.
    est_super_admin = models.BooleanField(default=False)

    # Champ technique (hors MCD métier, comme Jeton) : force un
    # changement de mot de passe à la prochaine connexion. Utilisé
    # quand un admin crée un compte avec un mot de passe temporaire
    # (ex: contrôleur externe) - décision prise ensemble.
    doit_changer_mot_de_passe = models.BooleanField(default=False)

    # Ajouté pour permettre le rapprochement automatique entre un
    # ParticipantCohorte importé (Excel/manuel, sans compte) et un
    # membre qui crée son compte plus tard - voir ParticipantCohorte.
    telephone = models.CharField(max_length=30, blank=True, null=True)

    # Association réflexive NOMME : ON DELETE SET NULL
    # (si l'admin nommant est supprimé, on ne supprime pas en cascade
    # tous les comptes qu'il a nommés admin, on oublie juste le lien)
    compte_nommant = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="comptes_nommes",
        db_column="id_compte_nommant",
    )

    class Meta:
        db_table = "COMPTE"

    def definir_mot_de_passe(self, mot_de_passe_clair):
        """Hache et stocke le mot de passe. Ne jamais stocker en clair."""
        self.mot_de_passe_hash = make_password(mot_de_passe_clair)

    def verifier_mot_de_passe(self, mot_de_passe_clair):
        return check_password(mot_de_passe_clair, self.mot_de_passe_hash)

    def __str__(self):
        return f"{self.prenom} {self.nom} <{self.email}>"

    @property
    def is_authenticated(self):
        """
        Django REST Framework s'attend à ce que tout objet représentant
        un utilisateur connecté possède cette propriété. Comme Compte
        n'hérite pas du système d'utilisateur natif de Django, on la
        déclare nous-mêmes. Elle vaut toujours True ici : si on a un
        objet Compte en main dans une vue, c'est justement parce que
        son jeton a déjà été validé (voir authentication.py).
        """
        return True


# ---------------------------------------------------------------------
# MEMBRE : adhérent du parti
# ---------------------------------------------------------------------
class Membre(models.Model):
    STATUT_CHOICES = [
        ("ACTIF", "Actif"),
        ("SUSPENDU", "Suspendu"),
        ("EXPIRE", "Expiré"),
    ]

    # Les membres AP2A ne se définissent pas par leur localité (officiels,
    # entrepreneurs, jeunes... répartis dans tout le pays) mais par leur
    # fonction au sein de l'association - remplace la Section comme champ
    # d'identité principal (Section reste disponible en secondaire, voir
    # plus bas).
    FONCTION_CHOICES = [
        ("PRESIDENT", "Président(e)"),
        ("VICE_PRESIDENT", "Vice-Président(e)"),
        ("SECRETAIRE_GENERAL", "Secrétaire Général(e)"),
        ("TRESORIER", "Trésorier(ère)"),
        ("MEMBRE_BUREAU_EXECUTIF", "Membre du Bureau Exécutif"),
        ("COORDINATEUR_COMMISSION", "Coordinateur/trice de Commission"),
        ("AMBASSADEUR", "Ambassadeur/drice"),
        ("MEMBRE_ACTIF", "Membre Actif"),
        ("MEMBRE_HONNEUR", "Membre d'Honneur"),
    ]

    # Fonctions considérées comme relevant de la direction de l'association,
    # utilisées pour dériver l'organigramme depuis l'annuaire (pas de
    # modèle séparé pour la structure du bureau).
    FONCTIONS_DIRECTION = [
        "PRESIDENT", "VICE_PRESIDENT", "SECRETAIRE_GENERAL",
        "TRESORIER", "MEMBRE_BUREAU_EXECUTIF",
    ]

    id_membre = models.AutoField(primary_key=True)
    numero_adherent = models.CharField(max_length=20, unique=True)
    photo = models.ImageField(upload_to="photos_membres/", null=True, blank=True)
    date_adhesion = models.DateField()
    statut_adhesion = models.CharField(
        max_length=20, choices=STATUT_CHOICES, default="ACTIF"
    )
    fonction_association = models.CharField(
        max_length=30, choices=FONCTION_CHOICES, default="MEMBRE_ACTIF"
    )

    # 1:1 vers COMPTE -> ON DELETE CASCADE (un membre n'existe pas
    # sans son compte). unique=True traduit la cardinalité (1,1).
    compte = models.OneToOneField(
        Compte,
        on_delete=models.CASCADE,
        related_name="membre",
        db_column="id_compte",
    )

    # 1:N vers SECTION -> ON DELETE RESTRICT (= PROTECT en Django).
    # Rendu optionnel : la fonction au sein d'AP2A est désormais le champ
    # d'identité principal, la section (région) n'est plus qu'une méta-
    # donnée secondaire facultative.
    section = models.ForeignKey(
        Section,
        on_delete=models.PROTECT,
        related_name="membres",
        db_column="id_section",
        null=True,
        blank=True,
    )

    # Association réflexive PARRAINE -> ON DELETE SET NULL
    parrain = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="filleuls",
        db_column="id_parrain",
    )

    class Meta:
        db_table = "MEMBRE"

    def __str__(self):
        return f"{self.compte.prenom} {self.compte.nom} - {self.numero_adherent}"


# ---------------------------------------------------------------------
# CONTROLEUR : agent de contrôle, pas forcément membre
# ---------------------------------------------------------------------
class Controleur(models.Model):
    id_controleur = models.AutoField(primary_key=True)
    zone_affectation = models.CharField(max_length=100, null=True, blank=True)
    date_nomination = models.DateField()

    compte = models.OneToOneField(
        Compte,
        on_delete=models.CASCADE,
        related_name="controleur",
        db_column="id_compte",
    )

    # Un contrôleur est assigné à UN SEUL événement à la fois. À la
    # clôture de cet événement, son compte est automatiquement suspendu
    # (voir vue_terminer_evenement) ; un admin le réaffecte ensuite à un
    # nouvel événement pour réactiver son compte (vue_assigner_controleur).
    evenement_assigne = models.ForeignKey(
        "Evenement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="controleurs_assignes",
        db_column="id_evenement_assigne",
    )

    class Meta:
        db_table = "CONTROLEUR"

    def __str__(self):
        return f"Contrôleur : {self.compte.prenom} {self.compte.nom}"


# ---------------------------------------------------------------------
# CARTE : support physique (QR ou NFC), historique par membre
# ---------------------------------------------------------------------
class Carte(models.Model):
    TYPE_CHOICES = [("QR", "QR Code"), ("NFC", "Puce NFC")]
    STATUT_CHOICES = [
        ("ACTIVE", "Active"),
        ("BLOQUEE", "Bloquée"),
        ("PERDUE", "Perdue"),
    ]

    id_carte = models.AutoField(primary_key=True)
    uuid = models.UUIDField(unique=True)
    type_carte = models.CharField(max_length=10, choices=TYPE_CHOICES)
    date_emission = models.DateField(auto_now_add=True)
    statut_carte = models.CharField(
        max_length=20, choices=STATUT_CHOICES, default="ACTIVE"
    )

    # Version de la cle HMAC utilisee pour signer cette carte.
    # Non sensible : juste un numero, jamais la cle elle-meme.
    id_version_cle = models.PositiveSmallIntegerField()

    # 1:N vers MEMBRE -> ON DELETE CASCADE (historique de cartes,
    # supprimé si le membre est supprimé)
    membre = models.ForeignKey(
        Membre,
        on_delete=models.CASCADE,
        related_name="cartes",
        db_column="id_membre",
    )

    class Meta:
        db_table = "CARTE"

    def __str__(self):
        return f"Carte {self.type_carte} - {self.uuid} ({self.statut_carte})"


# ---------------------------------------------------------------------
# EVENEMENT : meeting, congrès, AG - organisé par un admin
# ---------------------------------------------------------------------
class Evenement(models.Model):
    # Types élargis pour couvrir les activités ponctuelles de
    # l'association. FORMATION retiré : les formations ont leur
    # propre système dédié (Formation/Cohorte), plus adapté à leur
    # nature multi-sessions - les garder ici aurait été redondant
    # et source de confusion (deux façons différentes de créer
    # "une formation" dans l'app).
    TYPE_CHOICES = [
        ("MEETING", "Meeting"),
        ("CONGRES", "Congrès"),
        ("AG", "Assemblée générale"),
        ("REUNION", "Réunion"),
        ("GALA", "Gala"),
        ("DISTRIBUTION_MATERIEL", "Distribution de matériel"),
        ("MISSION_MEDICALE", "Mission médicale"),
        ("AUTRE", "Autre"),
    ]
    MODE_CHOICES = [
        ("OUVERT", "Ouvert à tous les membres"),
        ("SUR_INSCRIPTION", "Sur inscription préalable"),
        ("RESTREINT", "Restreint à une sélection de membres"),
    ]

    STATUT_CHOICES = [
        ("BROUILLON", "Brouillon"),
        ("PROGRAMME", "Programmé"),
        ("EN_COURS", "En cours"),
        ("TERMINE", "Terminé"),
        ("ANNULE", "Annulé"),
    ]

    id_evenement = models.AutoField(primary_key=True)
    titre = models.CharField(max_length=150)
    lieu = models.CharField(max_length=150)
    type_evenement = models.CharField(max_length=30, choices=TYPE_CHOICES)
    description = models.TextField(blank=True, null=True)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="PROGRAMME")
    est_termine = models.BooleanField(default=False)
    est_annule = models.BooleanField(default=False)
    date_fin = models.DateTimeField(null=True, blank=True)

    # Mode d'inscription : OUVERT = tout membre entre, on scanne à
    # l'arrivée. SUR_INSCRIPTION = le membre confirme sa venue à
    # l'avance depuis l'app (planification), mais le scan reste
    # obligatoire le jour J pour valider la présence effective.
    mode_inscription = models.CharField(
        max_length=20, choices=MODE_CHOICES, default="OUVERT",
    )
    # Capacité maximale (vide = illimitée). Si l'événement est sur
    # inscription et que la capacité est atteinte, les suivants sont
    # mis en liste d'attente.
    capacite_max = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Nombre max de participants (vide = illimité)",
    )

    compte_organisateur = models.ForeignKey(
        Compte,
        on_delete=models.PROTECT,
        related_name="evenements_organises",
        db_column="id_compte_organisateur",
    )

    # Pertinent seulement si mode_inscription="RESTREINT" : seuls ces
    # membres voient l'événement dans leur espace et sont notifiés.
    membres_cibles = models.ManyToManyField(
        Membre,
        blank=True,
        related_name="evenements_cibles",
    )

    class Meta:
        db_table = "EVENEMENT"

    def __str__(self):
        return self.titre


# ---------------------------------------------------------------------
# SEANCE : nouvelle entité, un événement se découpe en une ou plusieurs
# séances (dates). Un événement ponctuel (meeting, distribution) a une
# seule séance ; une formation sur 5 jours en a 5. C'est la séance,
# pas l'événement, qui porte la date et qui reçoit les présences.
# ---------------------------------------------------------------------
class Seance(models.Model):
    id_seance = models.AutoField(primary_key=True)
    date_seance = models.DateTimeField()
    numero_ordre = models.PositiveSmallIntegerField(default=1)

    evenement = models.ForeignKey(
        Evenement,
        on_delete=models.CASCADE,
        related_name="seances",
        db_column="id_evenement",
    )

    class Meta:
        db_table = "SEANCE"
        ordering = ["numero_ordre"]

    def __str__(self):
        return f"{self.evenement.titre} — séance {self.numero_ordre}"


# ---------------------------------------------------------------------
# COTISATION : historique des paiements d'adhésion
# ---------------------------------------------------------------------
class Cotisation(models.Model):
    id_cotisation = models.AutoField(primary_key=True)
    montant = models.DecimalField(max_digits=10, decimal_places=2)
    date_paiement = models.DateField()
    periode_couverte = models.CharField(max_length=20)

    membre = models.ForeignKey(
        Membre,
        on_delete=models.CASCADE,
        related_name="cotisations",
        db_column="id_membre",
    )

    class Meta:
        db_table = "COTISATION"

    def __str__(self):
        return f"{self.montant} FCFA - {self.periode_couverte}"


# ---------------------------------------------------------------------
# JOURNAL_AUDIT : traçabilité des actions sensibles
# Auteur = n'importe quel compte (admin OU membre agissant sur sa
# propre carte : perte, recherche manuelle...)
# ---------------------------------------------------------------------
class JournalAudit(models.Model):
    id_action = models.AutoField(primary_key=True)
    type_action = models.CharField(max_length=50)
    date_action = models.DateTimeField(auto_now_add=True)
    description = models.TextField(null=True, blank=True)

    compte_auteur = models.ForeignKey(
        Compte,
        on_delete=models.PROTECT,
        related_name="actions_journalisees",
        db_column="id_compte_auteur",
    )

    class Meta:
        db_table = "JOURNAL_AUDIT"
        ordering = ["-date_action"]

    def __str__(self):
        return f"[{self.date_action:%d/%m/%Y %H:%M}] {self.type_action}"


# ---------------------------------------------------------------------
# PARTICIPE : association N:M porteuse entre MEMBRE et EVENEMENT
# Clé primaire composite -> traduite en unique_together en Django
# (Django exige toujours un id technique, mais on force l'unicité
# de la paire pour reproduire fidèlement la contrainte du MLD)
# ---------------------------------------------------------------------
class Participe(models.Model):
    METHODE_CHOICES = [
        ("QR", "QR Code"),
        ("NFC", "Puce NFC"),
        ("MANUEL", "Recherche manuelle"),
    ]

    membre = models.ForeignKey(
        Membre,
        on_delete=models.CASCADE,
        related_name="participations",
        db_column="id_membre",
    )
    # CHANGÉ : pointe vers Seance, plus vers Evenement directement -
    # une présence est désormais rattachée à un jour précis, pas
    # seulement à l'événement dans son ensemble.
    seance = models.ForeignKey(
        Seance,
        on_delete=models.CASCADE,
        related_name="participants",
        db_column="id_seance",
    )
    heure_arrivee = models.DateTimeField()
    methode_scan = models.CharField(max_length=10, choices=METHODE_CHOICES)

    # NOT NULL, décidé ensemble : un scan est toujours fait par un
    # contrôleur identifié dans ce système
    controleur_scan = models.ForeignKey(
        Controleur,
        on_delete=models.PROTECT,
        related_name="scans_effectues",
        db_column="id_controleur_scan",
    )

    class Meta:
        db_table = "PARTICIPE"
        unique_together = (("membre", "seance"),)

    def __str__(self):
        return f"{self.membre} @ {self.seance}"


# =======================================================================
# TABLE TECHNIQUE (hors MCD métier) : gestion des sessions applicatives
# =======================================================================
class Jeton(models.Model):
    """
    Jeton d'authentification, généré à la connexion et vérifié à
    chaque requête protégée.

    Ce n'est PAS une entité du MCD : c'est un mécanisme purement
    technique, nécessaire au fonctionnement de l'authentification.

    Simplification volontaire : OneToOneField -> un compte n'a
    qu'UN SEUL jeton actif à la fois. Se reconnecter remplace
    l'ancien jeton (déconnexion automatique de toute autre session,
    par exemple si le membre se connecte sur un nouvel appareil).

    Seule l'EMPREINTE SHA-256 du jeton est stockée, jamais le jeton
    lui-même : une fuite de la base (sauvegarde, injection SQL...) ne
    donne aucune session utilisable. Un hachage rapide sans sel suffit
    ici, contrairement aux mots de passe : le jeton est tiré
    uniformément sur 256 bits, donc ni dictionnaire ni recherche
    exhaustive ne sont envisageables - il n'y a rien à ralentir.
    """

    empreinte = models.CharField(max_length=64, unique=True, editable=False)
    compte = models.OneToOneField(
        Compte, on_delete=models.CASCADE, related_name="jeton"
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_expiration = models.DateTimeField()

    class Meta:
        db_table = "JETON"

    @staticmethod
    def generer_cle() -> str:
        """
        secrets.token_hex(32) génère 32 octets aléatoires cryptographiquement
        sûrs (via le générateur du système d'exploitation, pas un simple
        random.random() qui n'est PAS conçu pour la sécurité), encodés en
        64 caractères hexadécimaux.
        """
        return secrets.token_hex(32)

    @staticmethod
    def empreinte_de(cle: str) -> str:
        return hashlib.sha256(cle.encode("utf-8")).hexdigest()

    @classmethod
    def ouvrir_session(cls, compte) -> str:
        """
        Crée (ou remplace) le jeton du compte et retourne la clé EN
        CLAIR - c'est la seule fois où elle existe côté serveur, elle
        n'est transmise qu'au client.
        """
        cle = cls.generer_cle()
        cls.objects.update_or_create(
            compte=compte,
            defaults={
                "empreinte": cls.empreinte_de(cle),
                "date_expiration": timezone.now() + timedelta(hours=settings.JETON_DUREE_VIE_HEURES),
            },
        )
        return cle

    @property
    def est_expire(self) -> bool:
        return timezone.now() >= self.date_expiration

    def __str__(self):
        return f"Jeton de {self.compte.email}"


class TentativeConnexion(models.Model):
    """
    Trace des ÉCHECS de connexion, pour limiter la recherche exhaustive
    de mots de passe (voir vue_connexion). Le compteur porte sur
    l'identifiant SAISI (qu'il corresponde ou non à un compte, pour ne
    pas révéler quels comptes existent) et sur l'adresse IP.
    """

    identifiant = models.CharField(max_length=150, db_index=True)
    adresse_ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    date_tentative = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "TENTATIVE_CONNEXION"


class TicketScanConsomme(models.Model):
    """
    Nonces des tickets de scan déjà utilisés (voir tickets.py) : un
    ticket ne peut confirmer qu'UNE présence. Les lignes plus vieilles
    que la durée de validité d'un ticket sont purgées au fil de l'eau -
    au-delà, la signature horodatée du ticket suffit à le rejeter.
    """

    nonce = models.CharField(max_length=64, unique=True)
    date_consommation = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "TICKET_SCAN_CONSOMME"


# =========================================================================
# FORMATION / COHORTE : sous-système séparé des EVENEMENT/SEANCE.
# Décision prise ensemble : une formation est un vrai parcours (avec
# plusieurs sessions dans le temps, des cohortes successives), assez
# différent d'un événement ponctuel pour mériter ses propres entités
# plutôt que de forcer FORMATION dans le moule EVENEMENT/SEANCE.
# =========================================================================

class Formation(models.Model):
    """
    Le "catalogue" : le cours en tant que concept, indépendant de ses
    sessions. Ex: "Couture pour débutantes" - peut être organisée
    plusieurs fois (plusieurs cohortes) dans le temps.

    Pur catalogue, volontairement SANS statut ni public cible : ces deux
    notions n'ont de sens qu'au niveau d'une session précise (Cohorte) -
    une même formation peut avoir, en même temps, une cohorte en cours
    pour un public et une autre encore en planification pour un autre.
    """
    id_formation = models.AutoField(primary_key=True)
    titre = models.CharField(max_length=150)
    code_reference = models.CharField(
        max_length=30, unique=True, null=True, blank=True,
        help_text='Ex: "FORM-2026-001", pour le suivi interne',
    )
    description = models.TextField(
        blank=True, null=True, help_text="Description et objectifs pédagogiques",
    )
    domaine = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Catégorie/thématique (ex: informatique, langues, gestion de projet)",
    )
    duree_heures = models.PositiveIntegerField(
        null=True, blank=True, help_text="Durée totale indicative en heures"
    )
    prerequis = models.TextField(
        blank=True, null=True,
        help_text="Diplôme, niveau requis, matériel à apporter...",
    )

    class Meta:
        db_table = "FORMATION"

    def __str__(self):
        return self.titre


class Cohorte(models.Model):
    """
    Une session précise d'une formation : un groupe donné, sur une
    période donnée. Formateur "principal" en texte libre ici (des
    intervenants supplémentaires avec rôle/rémunération seront gérés
    séparément - vague 2).
    """
    STATUT_CHOICES = [
        ("BROUILLON", "Brouillon"),
        ("PROGRAMMEE", "Programmée"),
        ("EN_COURS", "En cours"),
        ("TERMINEE", "Terminée"),
        ("ANNULEE", "Annulée"),
    ]

    id_cohorte = models.AutoField(primary_key=True)
    code_cohorte = models.CharField(
        max_length=50, unique=True, help_text='Ex: "Couture-2026-T1"'
    )
    date_debut = models.DateField()
    date_fin = models.DateField()
    lieu = models.CharField(max_length=150, help_text='Adresse/salle, ou "à définir"')
    formateur = models.CharField(max_length=150, blank=True, null=True)
    # BROUILLON par défaut : une cohorte nouvellement créée est en
    # planification tant qu'un admin n'a pas cliqué "Lancer la formation"
    # (vue_acter_programme_cohorte, qui notifie aussi les membres ciblés).
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="BROUILLON")
    contenu_kit = models.TextField(
        blank=True, null=True, help_text="Description du matériel et fournitures contenus dans le kit remis aux participants",
    )

    # --- Logistique ---
    capacite_max = models.PositiveIntegerField(
        null=True, blank=True, help_text="Nombre max de participants (vide = illimité)",
    )
    capacite_min = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Seuil minimal pour maintenir la session (en dessous, envisager l'annulation)",
    )
    materiel_necessaire = models.TextField(
        blank=True, null=True, help_text="Vidéoprojecteur, ordinateurs, salle équipée...",
    )

    # --- Certification et suivi ---
    seuil_certification = models.PositiveIntegerField(
        default=75,
        help_text="Pourcentage minimum de présence pour obtenir le certificat (laissé à l'appréciation des organisateurs)",
    )
    duree_suivi_jours = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Durée du suivi post-formation en jours, définie par les admins (vide = pas de suivi)",
    )

    # --- Inscriptions et tarification ---
    est_payante = models.BooleanField(
        default=True,
        help_text="Désactivé = formation gratuite, les champs prix sont ignorés",
    )
    prix = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Vide ou 0 = gratuite",
    )
    prix_adherent = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Tarif préférentiel adhérent, si différent du prix standard",
    )
    date_limite_inscription = models.DateField(null=True, blank=True)
    conditions_annulation = models.TextField(blank=True, null=True)

    # --- Public cible ---
    PUBLIC_CIBLE_CHOICES = [
        ("TOUS", "Tous les membres"),
        ("SPECIFIQUE", "Sélection de membres"),
    ]
    public_cible_type = models.CharField(
        max_length=20, choices=PUBLIC_CIBLE_CHOICES, default="TOUS",
    )
    membres_cibles = models.ManyToManyField(
        Membre,
        blank=True,
        related_name="cohortes_ciblees",
        help_text="Pertinent seulement si public_cible_type='SPECIFIQUE'",
    )

    # --- Reporting (pour l'association) ---
    financeur = models.CharField(
        max_length=150, blank=True, null=True, help_text="Bailleur associé, si formation subventionnée",
    )
    numero_convention = models.CharField(max_length=100, blank=True, null=True)

    # PROTECT : on ne supprime pas une formation tant que des cohortes
    # (donc un historique réel de sessions) y sont rattachées.
    formation = models.ForeignKey(
        Formation,
        on_delete=models.PROTECT,
        related_name="cohortes",
        db_column="id_formation",
    )

    class Meta:
        db_table = "COHORTE"

    def __str__(self):
        return self.code_cohorte


class Participant(models.Model):
    """
    Identité GLOBALE d'une personne suivant des formations, qu'elle
    ait ou non un compte - UNE SEULE FOIS par personne réelle, quel
    que soit le nombre de formations suivies. Reconnue via téléphone
    (clé principale) ou numéro de carte d'identité (repli, optionnel -
    tout le monde n'en a pas) pour ne jamais créer de doublon quand
    la même personne réapparaît dans un nouvel import.

    Porte UN SEUL badge (uuid signé, même mécanisme HMAC que CARTE,
    réutilisé tel quel) qui fonctionne pour TOUTES ses formations -
    pas un badge par session.
    """
    id_participant = models.AutoField(primary_key=True)
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    telephone = models.CharField(max_length=30, blank=True, null=True)
    numero_carte_identite = models.CharField(max_length=50, blank=True, null=True)
    # Numéro lisible par un humain (contrairement à l'uuid, illisible
    # et pas fait pour être tapé) - communiqué à la personne avec son
    # badge physique, et utilisé pour rattacher son compte plus tard.
    numero_badge = models.CharField(max_length=20, unique=True, null=True, blank=True)
    # Photo ajoutée par un organisateur OU par la personne elle-même
    # une fois qu'elle a un compte lié (voir vue_televerser_photo côté
    # membre, et vue_televerser_photo_participant côté admin).
    photo = models.ImageField(upload_to="photos_participants/", null=True, blank=True)

    uuid = models.UUIDField(unique=True)
    id_version_cle = models.PositiveSmallIntegerField()
    date_creation = models.DateTimeField(auto_now_add=True)

    # OneToOne (pas ForeignKey) : un participant correspond à AU PLUS
    # un compte, et un compte à AU PLUS un participant - cohérent avec
    # le fait qu'on ne veut jamais deux badges pour la même personne.
    membre = models.OneToOneField(
        Membre,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="participant_externe",
        db_column="id_membre",
    )

    class Meta:
        db_table = "PARTICIPANT"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Généré APRÈS le premier save (besoin de id_participant, fourni
        # par la base) : évite tout risque de collision qu'un compteur
        # calculé à l'avance pourrait créer en cas d'accès concurrent.
        if not self.numero_badge:
            self.numero_badge = f"BADGE-{self.id_participant:06d}"
            super().save(update_fields=["numero_badge"])

    def __str__(self):
        return f"{self.prenom} {self.nom}"


class InscriptionCohorte(models.Model):
    """
    Association PARTICIPANT <-> COHORTE : qui suit quelle session.
    Contrairement à l'ancienne version, l'identité (nom, téléphone...)
    ne vit plus ici mais sur PARTICIPANT - cette table ne fait que
    relier une personne déjà identifiée à une session précise, ce qui
    permet à la MÊME personne d'être inscrite à plusieurs formations
    sans jamais dupliquer son identité ni son badge.
    """
    SOURCE_CHOICES = [
        ("EXCEL", "Import Excel"),
        ("MANUEL", "Ajout manuel"),
        ("COMPTE", "Auto-inscription via compte"),
    ]
    STATUT_CHOICES = [
        ("INSCRIT", "Inscrit"),
        ("CONFIRME", "Confirmé"),
        ("ABANDON", "Abandon"),
    ]

    id_inscription = models.AutoField(primary_key=True)
    date_inscription = models.DateTimeField(auto_now_add=True)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="INSCRIT")
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="inscriptions",
        db_column="id_participant",
    )
    cohorte = models.ForeignKey(
        Cohorte,
        on_delete=models.CASCADE,
        related_name="inscriptions",
        db_column="id_cohorte",
    )

    kit_distribue = models.BooleanField(default=False)
    date_distribution_kit = models.DateTimeField(null=True, blank=True)
    remis_par = models.ForeignKey(
        Compte,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kits_remis",
        db_column="id_compte_remettant",
    )
    certificat_delivre = models.BooleanField(default=False)
    date_emission_certificat = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "INSCRIPTION_COHORTE"
        # Une même personne ne peut être inscrite QU'UNE FOIS à une
        # cohorte donnée - mais rien n'empêche plusieurs lignes pour
        # le même participant sur des cohortes DIFFÉRENTES, ce qui est
        # exactement l'objectif : pas de duplication entre formations.
        unique_together = (("participant", "cohorte"),)

    def __str__(self):
        return f"{self.participant} -> {self.cohorte}"


class Kit(models.Model):
    """
    Un kit pédagogique préparé par l'association (contenu libre : matériel,
    fournitures, supports...) et destiné à un public précis. Volontairement
    SÉPARÉ de Formation/Cohorte : donner un kit n'est pas systématique à
    chaque formation, donc on ne le prépare que lorsqu'il y en a besoin, et
    on choisit alors librement à qui il est destiné - toute une formation
    (ses cohortes actuelles et futures), une cohorte précise, ou seulement
    certains participants d'une cohorte.
    """
    CIBLE_FORMATION = "FORMATION"
    CIBLE_COHORTE = "COHORTE"
    CIBLE_PARTICIPANTS = "PARTICIPANTS"
    CIBLE_CHOICES = [
        (CIBLE_FORMATION, "Toute une formation"),
        (CIBLE_COHORTE, "Une cohorte précise"),
        (CIBLE_PARTICIPANTS, "Certains participants d'une cohorte"),
    ]

    id_kit = models.AutoField(primary_key=True)
    nom = models.CharField(max_length=150)
    contenu = models.TextField(help_text="Description du matériel et fournitures du kit")
    type_cible = models.CharField(max_length=20, choices=CIBLE_CHOICES)

    # Un seul de ces trois champs est renseigné, selon type_cible (voir
    # validation dans vue_creer_kit) : FORMATION -> formation, COHORTE ->
    # cohorte, PARTICIPANTS -> cohorte + participants_cibles (les personnes
    # ciblées doivent être inscrites à CETTE cohorte).
    formation = models.ForeignKey(
        Formation, on_delete=models.CASCADE, null=True, blank=True, related_name="kits",
    )
    cohorte = models.ForeignKey(
        Cohorte, on_delete=models.CASCADE, null=True, blank=True, related_name="kits",
    )
    participants_cibles = models.ManyToManyField(
        Participant, blank=True, related_name="kits_cibles",
    )

    cree_par = models.ForeignKey(
        Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name="kits_crees",
        db_column="id_compte_createur",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "KIT"
        ordering = ["-date_creation"]

    def __str__(self):
        return self.nom


class DistributionKit(models.Model):
    """
    Suivi de la remise RÉELLE d'un kit à un participant précis. Un Kit
    décrit QUOI et POUR QUI en général (voir Kit.type_cible) ; cette
    table répond à une question différente et par personne : est-ce que
    CETTE personne a VRAIMENT reçu le kit physiquement, quand, et remis
    par qui - ce qui permet à l'association de contrôler qui a reçu ou
    pas, y compris quand un même participant est concerné par plusieurs
    kits différents en même temps (un kit de toute la formation ET un
    kit spécifique à lui, par exemple).
    """
    id_distribution = models.AutoField(primary_key=True)
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name="distributions")
    participant = models.ForeignKey(
        Participant, on_delete=models.CASCADE, related_name="distributions_kits",
    )
    distribue = models.BooleanField(default=False)
    date_distribution = models.DateTimeField(null=True, blank=True)
    remis_par = models.ForeignKey(
        Compte, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="distributions_kits_remis", db_column="id_compte_remettant",
    )

    class Meta:
        db_table = "DISTRIBUTION_KIT"
        unique_together = (("kit", "participant"),)

    def __str__(self):
        return f"{self.kit} -> {self.participant}"


class SeanceCohorte(models.Model):
    """
    Une vraie séance de cours, pas juste une date : heure de
    début/fin, salle, intitulé et type, avec un formateur qui peut
    différer de celui par défaut de la cohorte (intervenant ponctuel).
    """
    TYPE_CHOICES = [
        ("COURS", "Cours"),
        ("TD", "Travaux dirigés"),
        ("TP", "Travaux pratiques"),
        ("EXAMEN", "Examen"),
    ]

    id_seance_cohorte = models.AutoField(primary_key=True)
    numero_ordre = models.PositiveSmallIntegerField(default=1)
    date_seance = models.DateTimeField(help_text="Date + heure de début")
    heure_fin = models.DateTimeField(null=True, blank=True)
    lieu = models.CharField(
        max_length=150, blank=True, null=True,
        help_text="Laisser vide pour reprendre le lieu par défaut de la cohorte",
    )
    titre_seance = models.CharField(max_length=150, blank=True, null=True)
    type_seance = models.CharField(max_length=10, choices=TYPE_CHOICES, default="COURS")
    formateur_seance = models.CharField(
        max_length=150, blank=True, null=True,
        help_text="Laisser vide pour reprendre le formateur par défaut de la cohorte",
    )

    cohorte = models.ForeignKey(
        Cohorte,
        on_delete=models.CASCADE,
        related_name="seances",
        db_column="id_cohorte",
    )

    class Meta:
        db_table = "SEANCE_COHORTE"
        ordering = ["numero_ordre"]

    def __str__(self):
        return f"{self.cohorte.code_cohorte} — séance {self.numero_ordre}"


class PresenceCohorte(models.Model):
    """
    Pointe directement vers PARTICIPANT (l'identité globale) - la
    présence à une séance concerne la PERSONNE, indépendamment de la
    formation dans laquelle on la regarde.
    """
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="presences",
        db_column="id_participant",
    )
    seance_cohorte = models.ForeignKey(
        SeanceCohorte,
        on_delete=models.CASCADE,
        related_name="presences",
        db_column="id_seance_cohorte",
    )
    heure_arrivee = models.DateTimeField()
    methode_scan = models.CharField(max_length=10, choices=Participe.METHODE_CHOICES)

    controleur_scan = models.ForeignKey(
        Controleur,
        on_delete=models.PROTECT,
        related_name="scans_cohortes",
        db_column="id_controleur_scan",
    )

    class Meta:
        db_table = "PRESENCE_COHORTE"
        unique_together = (("participant", "seance_cohorte"),)

    def __str__(self):
        return f"{self.participant} @ {self.seance_cohorte}"


# =========================================================================
# PERMISSION_ADMIN : permissions granulaires pour les admins désignés.
# Le super admin a TOUS les droits sans avoir besoin de lignes ici.
# Chaque ligne accorde UNE permission à UN admin désigné.
# =========================================================================

class PermissionAdmin(models.Model):
    """
    Table de liaison entre un Compte admin et une permission spécifique.
    Le super admin (est_super_admin=True) contourne cette table : il a
    automatiquement accès à tout. Cette table ne concerne que les admins
    désignés (est_admin=True, est_super_admin=False).
    """
    PERMISSION_CHOICES = [
        ("GERER_MEMBRES", "Gérer les membres"),
        ("GERER_EVENEMENTS", "Gérer les événements"),
        ("GERER_FORMATIONS", "Gérer les formations et cohortes"),
        ("GERER_ACTIONS_SOCIALES", "Gérer les actions sociales"),
        ("GERER_CONTROLEURS", "Gérer les contrôleurs"),
        ("VOIR_RAPPORTS", "Voir les rapports et statistiques"),
        ("IMPORTER_DONNEES", "Importer des données Excel"),
        ("GERER_SUIVI", "Gérer le suivi post-formation"),
        ("GERER_GOUVERNANCE", "Gérer la gouvernance (comptes-rendus du bureau)"),
        ("GERER_COMMUNICATION", "Gérer la communication (actualités publiques)"),
        ("GERER_REFERENTIELS", "Gérer les référentiels (zones, partenaires, types d'action)"),
        ("DONNEES_MEDICALES", "Voir et saisir les données médicales"),
        ("GERER_CIBLES", "Gérer les cibles (personnes, groupes, établissements...)"),
    ]

    id_permission = models.AutoField(primary_key=True)
    compte = models.ForeignKey(
        Compte,
        on_delete=models.CASCADE,
        related_name="permissions_admin",
        db_column="id_compte",
    )
    code_permission = models.CharField(max_length=30, choices=PERMISSION_CHOICES)
    date_attribution = models.DateTimeField(auto_now_add=True)

    # Qui a attribué cette permission (traçabilité)
    attribue_par = models.ForeignKey(
        Compte,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="permissions_attribuees",
        db_column="id_compte_attribuant",
    )

    class Meta:
        db_table = "PERMISSION_ADMIN"
        unique_together = (("compte", "code_permission"),)

    def __str__(self):
        return f"{self.compte.prenom} {self.compte.nom} → {self.code_permission}"


# =========================================================================
# CONFIRMATION_EVENEMENT : un membre déclare son intention de venir
# (planification), mais ça NE REMPLACE PAS le scan le jour J.
# =========================================================================

class ConfirmationEvenement(models.Model):
    """
    Déclaration d'intention : le membre confirme qu'il prévoit de venir.
    Utile pour les organisateurs pour anticiper la logistique (nombre de
    chaises, repas, etc.). La présence effective reste validée par scan.
    """
    STATUT_CHOICES = [
        ("CONFIRME", "Confirmé"),
        ("LISTE_ATTENTE", "En liste d'attente"),
        ("ANNULE", "Annulé par le membre"),
    ]

    id_confirmation = models.AutoField(primary_key=True)
    membre = models.ForeignKey(
        Membre,
        on_delete=models.CASCADE,
        related_name="confirmations_evenements",
        db_column="id_membre",
    )
    evenement = models.ForeignKey(
        Evenement,
        on_delete=models.CASCADE,
        related_name="confirmations",
        db_column="id_evenement",
    )
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="CONFIRME")
    date_confirmation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "CONFIRMATION_EVENEMENT"
        unique_together = (("membre", "evenement"),)

    def __str__(self):
        return f"{self.membre} → {self.evenement.titre} ({self.statut})"


# =========================================================================
# INVITE_EXTERNE : personne non-membre enregistrée pour un événement
# par un admin (invité d'honneur, partenaire, etc.)
# =========================================================================

class InviteExterne(models.Model):
    id_invite = models.AutoField(primary_key=True)
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    telephone = models.CharField(max_length=30, blank=True, null=True)
    organisation = models.CharField(max_length=150, blank=True, null=True)

    evenement = models.ForeignKey(
        Evenement,
        on_delete=models.CASCADE,
        related_name="invites_externes",
        db_column="id_evenement",
    )
    est_present = models.BooleanField(default=False)
    date_enregistrement = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "INVITE_EXTERNE"

    def __str__(self):
        return f"{self.prenom} {self.nom} (invité - {self.evenement.titre})"


# =========================================================================
# ACTIONS SOCIALES : module complet pour les programmes d'aide
# (commerce, atelier, médical, autre)
# =========================================================================

class ActionSociale(models.Model):
    """
    Un programme d'aide organisé par l'association : aide à la création
    de commerce, mise en place d'atelier, mission médicale, etc.
    """
    TYPE_CHOICES = [
        ("DON", "Distribution de dons alimentaires / matériels"),
        ("COMMERCE", "Aide à la création de commerce"),
        ("ATELIER", "Aide à l'ouverture d'atelier"),
        ("MEDICAL", "Consultation / traitement médical"),
        ("AUTRE", "Autre action sociale"),
    ]
    STATUT_CHOICES = [
        ("PLANIFIE", "Planifié"),
        ("EN_COURS", "En cours"),
        ("TERMINE", "Terminé"),
        ("ANNULE", "Annulé"),
    ]

    id_action = models.AutoField(primary_key=True)
    titre = models.CharField(max_length=150)
    type_action = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.TextField(blank=True, null=True)
    lieu = models.CharField(max_length=150, blank=True, null=True)
    date_debut = models.DateField()
    date_fin = models.DateField(null=True, blank=True)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="PLANIFIE")
    contenu_don = models.TextField(
        blank=True, null=True,
        help_text="Ce qui est distribué (ex: 25kg de riz, 5L d'huile, fournitures, semences...)",
    )

    compte_organisateur = models.ForeignKey(
        Compte,
        on_delete=models.PROTECT,
        related_name="actions_sociales_organisees",
        db_column="id_compte_organisateur",
    )

    class Meta:
        db_table = "ACTION_SOCIALE"
        ordering = ["-date_debut"]

    def __str__(self):
        return f"{self.titre} ({self.get_type_action_display()})"


class Beneficiaire(models.Model):
    """
    Identité GLOBALE d'une personne bénéficiant d'actions sociales.
    Comme Participant pour les formations : UNE SEULE FOIS par personne
    réelle, identifiée par téléphone pour éviter les doublons.
    Un bénéficiaire peut être un membre de l'association ou non.
    """
    SEXE_CHOICES = [
        ("M", "Masculin"),
        ("F", "Féminin"),
    ]

    id_beneficiaire = models.AutoField(primary_key=True)
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    telephone = models.CharField(max_length=30, blank=True, null=True)
    sexe = models.CharField(max_length=1, choices=SEXE_CHOICES, blank=True, null=True)
    date_naissance = models.DateField(null=True, blank=True)
    numero_identification = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="Numéro de carte d'identité (optionnel)",
    )
    adresse = models.TextField(blank=True, null=True)
    date_enregistrement = models.DateTimeField(auto_now_add=True)

    # Lien optionnel vers un membre de l'association
    membre = models.OneToOneField(
        Membre,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="beneficiaire",
        db_column="id_membre",
    )

    class Meta:
        db_table = "BENEFICIAIRE"

    def __str__(self):
        return f"{self.prenom} {self.nom}"


class ParticipationAction(models.Model):
    """
    Lien entre un bénéficiaire et une action sociale.
    Porte le statut de suivi et les notes pour cette aide spécifique.
    """
    STATUT_CHOICES = [
        ("EN_ATTENTE", "En attente"),
        ("EN_COURS", "En cours de suivi"),
        ("TERMINE", "Terminé avec succès"),
        ("ABANDON", "Abandonné"),
    ]

    id_participation = models.AutoField(primary_key=True)
    beneficiaire = models.ForeignKey(
        Beneficiaire,
        on_delete=models.CASCADE,
        related_name="participations",
        db_column="id_beneficiaire",
    )
    action = models.ForeignKey(
        ActionSociale,
        on_delete=models.CASCADE,
        related_name="participations",
        db_column="id_action",
    )
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="EN_ATTENTE")
    date_inscription = models.DateTimeField(auto_now_add=True)
    date_derniere_maj = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True, null=True)
    type_aide_recue = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Description de l'aide concrète fournie",
    )
    don_recu = models.BooleanField(default=False)
    date_reception_don = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "PARTICIPATION_ACTION"
        unique_together = (("beneficiaire", "action"),)

    def __str__(self):
        return f"{self.beneficiaire} → {self.action.titre}"


class DetailCommerce(models.Model):
    """
    Champs spécifiques pour une aide à la création de commerce.
    Lié 1:1 à une ParticipationAction de type COMMERCE.
    """
    participation = models.OneToOneField(
        ParticipationAction,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="detail_commerce",
        db_column="id_participation",
    )
    type_commerce = models.CharField(max_length=150)
    localisation = models.CharField(max_length=200, blank=True, null=True)
    capital_depart_fourni = models.BooleanField(default=False)
    montant_accompagnement = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    commerce_operationnel = models.BooleanField(
        default=False,
        help_text="Le commerce est-il effectivement ouvert et en activité ?",
    )

    class Meta:
        db_table = "DETAIL_COMMERCE"

    def __str__(self):
        return f"Commerce : {self.type_commerce}"


class DetailAtelier(models.Model):
    """
    Champs spécifiques pour une aide à l'ouverture d'atelier.
    """
    participation = models.OneToOneField(
        ParticipationAction,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="detail_atelier",
        db_column="id_participation",
    )
    domaine_atelier = models.CharField(max_length=150)
    equipement_fourni = models.TextField(
        blank=True, null=True,
        help_text="Liste du matériel/équipement fourni",
    )
    local_mis_a_disposition = models.BooleanField(default=False)
    atelier_operationnel = models.BooleanField(
        default=False,
        help_text="L'atelier est-il effectivement ouvert et en activité ?",
    )

    class Meta:
        db_table = "DETAIL_ATELIER"

    def __str__(self):
        return f"Atelier : {self.domaine_atelier}"


class DetailMedical(models.Model):
    """
    Champs spécifiques pour une consultation/traitement médical.
    PAS de données médicales sensibles (diagnostic, pathologie) :
    on enregistre uniquement le fait qu'une consultation a eu lieu,
    le type de soin et si un traitement a été fourni.
    """
    TYPE_SOIN_CHOICES = [
        ("CONSULTATION", "Consultation"),
        ("TRAITEMENT", "Traitement"),
        ("MEDICAMENTS", "Remise de médicaments"),
        ("CONSULTATION_TRAITEMENT", "Consultation + traitement"),
    ]

    participation = models.OneToOneField(
        ParticipationAction,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="detail_medical",
        db_column="id_participation",
    )
    date_consultation = models.DateField()
    type_soin = models.CharField(max_length=30, choices=TYPE_SOIN_CHOICES)
    medecin_referent = models.CharField(max_length=150, blank=True, null=True)
    a_ete_visite = models.BooleanField(default=False)
    date_visite = models.DateField(null=True, blank=True)
    necessite_traitement = models.BooleanField(default=False)
    description_besoin_traitement = models.TextField(
        blank=True, null=True,
        help_text="Besoins de traitement ou orientation identifiés",
    )
    traitement_fourni = models.BooleanField(default=False)
    traitement_effectue = models.BooleanField(default=False)
    date_traitement = models.DateField(null=True, blank=True)
    notes_suivi = models.TextField(
        blank=True, null=True,
        help_text="Notes de suivi (sans information médicale identifiable)",
    )

    class Meta:
        db_table = "DETAIL_MEDICAL"

    def __str__(self):
        return f"Médical : {self.get_type_soin_display()}"


# =========================================================================
# SUIVI POST-FORMATION : fiches de suivi remplies par un agent de
# l'association à des points de contrôle après la fin d'une cohorte.
# Durée et fréquence définies par les admins.
# =========================================================================

class SuiviPostFormation(models.Model):
    """
    Fiche de suivi individuelle : un agent de l'association contacte
    ou visite un ancien participant pour évaluer l'impact de la
    formation et l'utilisation du kit.
    """
    MOYEN_CONTACT_CHOICES = [
        ("VISITE", "Visite terrain"),
        ("APPEL", "Appel téléphonique"),
        ("MESSAGE", "Message (SMS/WhatsApp)"),
    ]
    STATUT_CHOICES = [
        ("EN_COURS", "Suivi en cours"),
        ("STABLE", "Situation stable"),
        ("ABANDONNE", "Participant a abandonné"),
        ("SUCCES", "Objectif atteint"),
    ]

    id_suivi = models.AutoField(primary_key=True)
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="suivis_post_formation",
        db_column="id_participant",
    )
    cohorte = models.ForeignKey(
        Cohorte,
        on_delete=models.CASCADE,
        related_name="suivis_post_formation",
        db_column="id_cohorte",
    )
    date_suivi = models.DateField()
    effectue_par = models.ForeignKey(
        Compte,
        on_delete=models.PROTECT,
        related_name="suivis_effectues",
        db_column="id_compte_effectuant",
    )
    moyen_contact = models.CharField(max_length=10, choices=MOYEN_CONTACT_CHOICES)

    # Indicateurs de suivi
    kit_remis = models.BooleanField(default=False)
    certificat_emis = models.BooleanField(default=False)
    activite_lancee = models.BooleanField(default=False)
    type_activite = models.CharField(
        max_length=150, blank=True, null=True,
        help_text="Si activité lancée : quel type d'activité",
    )
    localisation_activite = models.CharField(max_length=200, blank=True, null=True)
    difficultes = models.TextField(
        blank=True, null=True,
        help_text="Difficultés rencontrées par le participant",
    )
    niveau_satisfaction = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Note de 1 (très insatisfait) à 5 (très satisfait)",
    )
    recommandations = models.TextField(blank=True, null=True)
    statut_global = models.CharField(
        max_length=20, choices=STATUT_CHOICES, default="EN_COURS",
    )

    class Meta:
        db_table = "SUIVI_POST_FORMATION"
        ordering = ["-date_suivi"]

    def __str__(self):
        return f"Suivi {self.participant} — {self.cohorte} ({self.date_suivi})"


# =========================================================================
# NOTIFICATION : messages in-app pour les membres et admins.
# Stockées en base, affichées avec badge dans l'application.
# =========================================================================

class Notification(models.Model):
    TYPE_CHOICES = [
        ("INFO", "Information"),
        ("RAPPEL", "Rappel"),
        ("ALERTE", "Alerte"),
        ("SUCCES", "Succès"),
    ]

    id_notification = models.AutoField(primary_key=True)
    compte_destinataire = models.ForeignKey(
        Compte,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_column="id_compte_destinataire",
    )
    titre = models.CharField(max_length=200)
    corps = models.TextField()
    type_notification = models.CharField(max_length=10, choices=TYPE_CHOICES, default="INFO")
    lu = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)
    # Lien optionnel pour rediriger l'utilisateur vers l'écran concerné
    lien_action = models.CharField(
        max_length=200, blank=True, null=True,
        help_text="Route ou identifiant pour navigation dans l'app",
    )

    class Meta:
        db_table = "NOTIFICATION"
        ordering = ["-date_creation"]

    def __str__(self):
        etat = "🔵" if not self.lu else "✅"
        return f"{etat} {self.titre} → {self.compte_destinataire.prenom}"


# =========================================================================
# CONFIG_ASSOCIATION : paramètres globaux de l'association, singleton.
# Permet de modifier le comportement de l'app sans toucher au code :
# activer/désactiver la cotisation, définir des valeurs par défaut, etc.
# =========================================================================

class ConfigAssociation(models.Model):
    """
    Table singleton (une seule ligne) qui stocke les paramètres
    configurables de l'association. Créée automatiquement au premier
    accès via ConfigAssociation.charger().
    """
    # Cotisation (masquée pour l'instant, activable plus tard)
    cotisation_active = models.BooleanField(
        default=False,
        help_text="Activer la gestion des cotisations (masquée par défaut)",
    )
    montant_cotisation_annuel = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )

    # Valeurs par défaut
    seuil_certification_defaut = models.PositiveIntegerField(
        default=75,
        help_text="Seuil de présence par défaut pour la certification (%)",
    )
    duree_suivi_defaut_jours = models.PositiveIntegerField(
        default=180,
        help_text="Durée de suivi post-formation par défaut (en jours)",
    )

    # Informations de l'association (affichées sur les certificats)
    nom_association = models.CharField(max_length=200, default="AP2A")
    slogan = models.CharField(max_length=300, blank=True, null=True)
    logo = models.ImageField(upload_to="config/", null=True, blank=True)
    adresse = models.TextField(blank=True, null=True)
    email_contact = models.EmailField(blank=True, null=True)
    telephone_contact = models.CharField(max_length=30, blank=True, null=True)

    class Meta:
        db_table = "CONFIG_ASSOCIATION"
        verbose_name = "Configuration de l'association"
        verbose_name_plural = "Configuration de l'association"

    @classmethod
    def charger(cls):
        """
        Retourne l'unique instance de configuration. La crée avec les
        valeurs par défaut si elle n'existe pas encore.
        """
        config, _ = cls.objects.get_or_create(pk=1)
        return config


# =========================================================================
# GOUVERNANCE : comptes-rendus de réunions du bureau exécutif.
# L'organigramme lui-même n'a pas de modèle dédié - il est dérivé de
# Membre.fonction_association (voir Membre.FONCTIONS_DIRECTION).
# =========================================================================

class CompteRendu(models.Model):
    id_compte_rendu = models.AutoField(primary_key=True)
    titre = models.CharField(max_length=200)
    date_reunion = models.DateField()
    contenu = models.TextField()
    decisions = models.TextField(blank=True, null=True)
    cree_par = models.ForeignKey(
        Compte, on_delete=models.PROTECT,
        related_name="comptes_rendus_crees", db_column="id_compte_createur",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "COMPTE_RENDU"
        ordering = ["-date_reunion"]

    def __str__(self):
        return f"{self.titre} ({self.date_reunion})"


# =========================================================================
# ACTUALITES : communiqués/actualités publiés par l'association, exposés
# sur le site public (lecture publique, écriture admin uniquement).
# =========================================================================

class Actualite(models.Model):
    id_actualite = models.AutoField(primary_key=True)
    titre = models.CharField(max_length=200)
    contenu = models.TextField()
    image = models.ImageField(upload_to="actualites/", null=True, blank=True)
    date_publication = models.DateTimeField(default=timezone.now)
    publie_par = models.ForeignKey(
        Compte, on_delete=models.PROTECT,
        related_name="actualites_publiees", db_column="id_compte_auteur",
    )

    class Meta:
        db_table = "ACTUALITE"
        ordering = ["-date_publication"]

    def __str__(self):
        return self.titre

    def __str__(self):
        return f"Configuration {self.nom_association}"

# =========================================================================
# RÉFÉRENTIELS DU SUIVI DES ACTIONS (étape 1)
#
# L'association enregistre des cibles (personnes, groupes, ASC,
# établissements, organisations, zones sinistrées) et mène pour elles des
# actions de natures très différentes (formation, kits scolaires,
# réhabilitation d'école, appui aux sinistrés, campagne médicale...),
# souvent avec des partenaires. Ces tables sont le paramétrage commun :
# où (ZONE), avec qui (PARTENAIRE), quoi (TYPE_ACTION) et comment on
# mesure (DEFINITION_INDICATEUR, propre à chaque type d'action).
# =========================================================================

class Zone(models.Model):
    """
    Découpage administratif du Sénégal, hiérarchique : région >
    département > commune > quartier/village. Les 14 régions et 46
    départements sont préchargés ; communes et quartiers sont ajoutés
    par l'association au fil de ses interventions.
    """

    NIVEAU_CHOICES = [
        ("REGION", "Région"),
        ("DEPARTEMENT", "Département"),
        ("COMMUNE", "Commune"),
        ("QUARTIER", "Quartier / village"),
    ]
    # Niveau attendu du parent pour chaque niveau (None = racine).
    NIVEAU_PARENT = {"REGION": None, "DEPARTEMENT": "REGION", "COMMUNE": "DEPARTEMENT", "QUARTIER": "COMMUNE"}

    id_zone = models.AutoField(primary_key=True)
    nom = models.CharField(max_length=120)
    niveau = models.CharField(max_length=20, choices=NIVEAU_CHOICES)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="sous_zones", db_column="id_zone_parent",
    )
    actif = models.BooleanField(default=True)

    class Meta:
        db_table = "ZONE"
        ordering = ["nom"]
        constraints = [
            models.UniqueConstraint(fields=["parent", "niveau", "nom"], name="zone_unique_par_parent"),
            # Les régions n'ont pas de parent : NULL n'étant égal à rien en
            # SQL, la contrainte ci-dessus ne les protège pas des doublons.
            models.UniqueConstraint(
                fields=["niveau", "nom"], condition=models.Q(parent__isnull=True), name="zone_racine_unique",
            ),
        ]

    def chemin(self) -> str:
        """Ex: "Dakar > Pikine > Thiaroye" (de la région à cette zone)."""
        noms, zone = [], self
        while zone is not None:
            noms.append(zone.nom)
            zone = zone.parent
        return " > ".join(reversed(noms))

    def __str__(self):
        return self.chemin()


class Partenaire(models.Model):
    """
    Organisation avec laquelle l'association mène ses actions :
    organisme de formation, structure de santé, bailleur, collectivité...
    (Les comptes d'accès des partenaires viendront avec l'étape 5.)
    """

    TYPE_CHOICES = [
        ("ONG", "ONG / association"),
        ("ETAT", "Service de l'État"),
        ("COLLECTIVITE", "Collectivité territoriale"),
        ("SANTE", "Structure de santé"),
        ("FORMATION", "Organisme de formation"),
        ("ENTREPRISE", "Entreprise / secteur privé"),
        ("BAILLEUR", "Bailleur / fondation"),
        ("COMMUNAUTAIRE", "Organisation communautaire"),
        ("AUTRE", "Autre"),
    ]

    id_partenaire = models.AutoField(primary_key=True)
    nom = models.CharField(max_length=150, unique=True)
    sigle = models.CharField(max_length=30, blank=True, default="")
    type_partenaire = models.CharField(max_length=20, choices=TYPE_CHOICES)
    domaines = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Domaines d'intervention, ex: santé, formation professionnelle",
    )
    nom_contact = models.CharField(max_length=150, blank=True, default="")
    telephone = models.CharField(max_length=30, blank=True, default="")
    email = models.EmailField(max_length=150, blank=True, default="")
    adresse = models.CharField(max_length=255, blank=True, default="")
    zone = models.ForeignKey(
        Zone, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="partenaires", db_column="id_zone",
    )
    notes = models.TextField(blank=True, default="")
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "PARTENAIRE"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


TYPES_CIBLE_CHOICES = [
    ("PERSONNE", "Personne"),
    ("GROUPE", "Groupe (GIE, groupement de femmes, groupe de jeunes...)"),
    ("ASC", "ASC (association sportive et culturelle)"),
    ("ETABLISSEMENT", "Établissement (école, poste de santé...)"),
    ("ORGANISATION", "Organisation"),
    ("ZONE_SINISTREE", "Zone sinistrée / localité"),
]


class TypeAction(models.Model):
    """
    Nature d'une action, paramétrable par un admin : chaque type porte
    ses propres indicateurs (DefinitionIndicateur) et la liste des types
    de cibles auxquels il s'adresse.
    """

    CATEGORIE_CHOICES = [
        ("FORMATION", "Formation / renforcement de capacités"),
        ("EDUCATION", "Éducation"),
        ("SANTE", "Santé"),
        ("URGENCE", "Urgence / catastrophe"),
        ("ECONOMIE", "Insertion économique"),
        ("SOCIAL", "Action sociale / dons"),
        ("INFRASTRUCTURE", "Infrastructure"),
        ("AUTRE", "Autre"),
    ]

    id_type_action = models.AutoField(primary_key=True)
    code = models.SlugField(max_length=40, unique=True)
    libelle = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    categorie = models.CharField(max_length=20, choices=CATEGORIE_CHOICES, default="AUTRE")
    types_cible = models.JSONField(
        default=list,
        help_text="Codes de TYPES_CIBLE_CHOICES auxquels ce type d'action s'adresse",
    )
    # Une action de ce type s'appuie sur le module Formation/Cohorte
    # (séances, présences, certificats) en plus du suivi générique.
    est_formation = models.BooleanField(default=False)
    actif = models.BooleanField(default=True)

    class Meta:
        db_table = "TYPE_ACTION"
        ordering = ["libelle"]

    def __str__(self):
        return self.libelle


class DefinitionIndicateur(models.Model):
    """
    Un indicateur propre à un type d'action, ex: "Kits distribués"
    (nombre, kits) pour Fournitures scolaires, ou "Issue de la prise en
    charge" (choix) pour Campagne médicale.

    - moment : quand il est renseigné - situation de RÉFÉRENCE (avant
      l'action), à l'INTERVENTION, ou lors d'un SUIVI ultérieur.
    - niveau : pour CHAQUE CIBLE (une valeur par école, par ménage...)
      ou une seule fois pour l'ACTION entière (budget, nombre de séances).
    - sensible : donnée médicale/personnelle, visible uniquement avec la
      permission DONNEES_MEDICALES.
    """

    TYPE_VALEUR_CHOICES = [
        ("ENTIER", "Nombre entier"),
        ("DECIMAL", "Nombre décimal"),
        ("MONTANT", "Montant (FCFA)"),
        ("POURCENTAGE", "Pourcentage"),
        ("BOOLEEN", "Oui / non"),
        ("CHOIX", "Choix dans une liste"),
        ("TEXTE", "Texte"),
        ("DATE", "Date"),
    ]
    MOMENT_CHOICES = [
        ("REFERENCE", "Situation de référence (avant)"),
        ("INTERVENTION", "Intervention"),
        ("SUIVI", "Suivi"),
    ]
    NIVEAU_CHOICES = [
        ("CIBLE", "Par cible"),
        ("ACTION", "Pour l'action entière"),
    ]

    id_indicateur = models.AutoField(primary_key=True)
    type_action = models.ForeignKey(
        TypeAction, on_delete=models.CASCADE, related_name="indicateurs", db_column="id_type_action",
    )
    code = models.SlugField(max_length=60)
    libelle = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    type_valeur = models.CharField(max_length=20, choices=TYPE_VALEUR_CHOICES)
    unite = models.CharField(max_length=30, blank=True, default="")
    choix = models.JSONField(default=list, blank=True, help_text="Valeurs possibles si type_valeur = CHOIX")
    moment = models.CharField(max_length=20, choices=MOMENT_CHOICES, default="INTERVENTION")
    niveau = models.CharField(max_length=10, choices=NIVEAU_CHOICES, default="CIBLE")
    obligatoire = models.BooleanField(default=False)
    sensible = models.BooleanField(default=False)
    ordre = models.PositiveSmallIntegerField(default=0)
    actif = models.BooleanField(default=True)

    class Meta:
        db_table = "DEFINITION_INDICATEUR"
        ordering = ["type_action", "moment", "ordre", "id_indicateur"]
        constraints = [
            models.UniqueConstraint(fields=["type_action", "code"], name="indicateur_code_unique_par_type"),
        ]

    def __str__(self):
        return f"{self.type_action.code}.{self.code}"


# =========================================================================
# CIBLES (étape 2) : qui bénéficie des actions de l'association.
#
# Une seule notion pour six natures très différentes : une personne, un
# groupe (GIE, groupement de femmes...), une ASC, un établissement (école,
# poste de santé), une organisation, ou une zone sinistrée. Une cible a un
# historique : plusieurs actions dans le temps (fournitures puis
# réhabilitation pour une même école...).
# =========================================================================

class Cible(models.Model):
    SEXE_CHOICES = [("M", "Masculin"), ("F", "Féminin")]

    id_cible = models.AutoField(primary_key=True)
    type_cible = models.CharField(max_length=20, choices=TYPES_CIBLE_CHOICES)
    # Personne : nom de famille. Autres types : nom de la cible
    # ("École élémentaire de Thiaroye 2", "GIE Jappo"...).
    nom = models.CharField(max_length=150)
    # Champs propres aux personnes.
    prenom = models.CharField(max_length=100, blank=True, default="")
    sexe = models.CharField(max_length=1, choices=SEXE_CHOICES, blank=True, default="")
    date_naissance = models.DateField(null=True, blank=True)
    numero_identification = models.CharField(
        max_length=50, blank=True, default="", help_text="CNI ou autre pièce (optionnel)",
    )
    # Champs propres aux collectifs (groupe, ASC, établissement...).
    sous_type = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Précision libre : école élémentaire, GIE, groupement de femmes, daara...",
    )
    responsable = models.CharField(max_length=150, blank=True, default="")
    effectif = models.PositiveIntegerField(
        null=True, blank=True, help_text="Membres, élèves, habitants... selon le type",
    )
    # Contact et localisation, communs à tous.
    telephone = models.CharField(max_length=30, blank=True, default="")
    # Formes normalisées (sans accents ni casse, téléphone sans +221),
    # calculées à l'enregistrement : dédoublonnage et recherche.
    telephone_normalise = models.CharField(max_length=20, blank=True, default="", db_index=True, editable=False)
    nom_normalise = models.CharField(max_length=150, blank=True, default="", db_index=True, editable=False)
    prenom_normalise = models.CharField(max_length=100, blank=True, default="", editable=False)
    email = models.EmailField(max_length=150, blank=True, default="")
    zone = models.ForeignKey(
        Zone, null=True, blank=True, on_delete=models.SET_NULL, related_name="cibles", db_column="id_zone",
    )
    adresse = models.CharField(max_length=255, blank=True, default="")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    # Liens avec l'existant : membre de l'association, et bénéficiaire
    # des anciennes actions sociales repris lors de la migration.
    membre = models.OneToOneField(
        Membre, null=True, blank=True, on_delete=models.SET_NULL, related_name="cible", db_column="id_membre",
    )
    beneficiaire_origine = models.OneToOneField(
        Beneficiaire, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="cible", db_column="id_beneficiaire",
    )

    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    cree_par = models.ForeignKey(
        Compte, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="cibles_creees", db_column="id_compte_createur",
    )

    class Meta:
        db_table = "CIBLE"
        ordering = ["nom", "prenom"]
        indexes = [models.Index(fields=["type_cible", "nom"])]

    @property
    def est_personne(self) -> bool:
        return self.type_cible == "PERSONNE"

    @property
    def nom_complet(self) -> str:
        return f"{self.prenom} {self.nom}".strip() if self.est_personne else self.nom

    def save(self, *args, **kwargs):
        from .cibles import normaliser_telephone, normaliser_texte
        self.telephone_normalise = normaliser_telephone(self.telephone)
        self.nom_normalise = normaliser_texte(self.nom)
        self.prenom_normalise = normaliser_texte(self.prenom)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nom_complet


class Appartenance(models.Model):
    """
    Une personne fait partie d'un collectif : membre d'un GIE ou d'une
    ASC, élève d'une école, habitant d'une zone sinistrée...
    """

    id_appartenance = models.AutoField(primary_key=True)
    personne = models.ForeignKey(
        Cible, on_delete=models.CASCADE, related_name="appartenances", db_column="id_cible_personne",
    )
    collectif = models.ForeignKey(
        Cible, on_delete=models.CASCADE, related_name="membres_collectif", db_column="id_cible_collectif",
    )
    role = models.CharField(max_length=100, blank=True, default="", help_text="Présidente, élève, trésorier...")
    date_debut = models.DateField(null=True, blank=True)
    date_fin = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "APPARTENANCE"
        constraints = [
            models.UniqueConstraint(fields=["personne", "collectif"], name="appartenance_unique"),
        ]

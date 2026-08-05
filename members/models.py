from django.db import models
import uuid

class Membre(models.Model):
    STATUT_CHOICES = [
        ('ACTIF', 'Actif / Cotisation à jour'),
        ('SUSPENDU', 'Suspendu'),
        ('DESACTIVE', 'Désactivé'),
        ('PERDU', 'Carte Perdue / Volée'),
    ]

    uuid_membre = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    role = models.CharField(max_length=100, default="Militant")
    
    # Numéro physique de la puce NFC (ex: 04:A1:2B:3C)
    card_nfc_uid = models.CharField(max_length=50, blank=True, null=True, unique=True)
    
    # Statut pour le contrôle
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default='ACTIF')

    def __str__(self):
        return f"{self.prenom} {self.nom} - {self.statut}"
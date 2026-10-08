"""
adhesion/signals.py

Tant que les anciennes actions sociales existent (remplacées à
l'étape 3), tout nouveau bénéficiaire devient aussi une cible, pour que
la fiche de la personne et son historique restent complets.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Beneficiaire


@receiver(post_save, sender=Beneficiaire)
def creer_cible_pour_beneficiaire(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from .cibles import cible_pour_beneficiaire
        cible_pour_beneficiaire(instance)

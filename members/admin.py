from django.contrib import admin
from .models import Membre

@admin.register(Membre)
class MembreAdmin(admin.ModelAdmin):
    # Les colonnes à afficher dans la liste
    list_display = ('prenom', 'nom', 'statut', 'role', 'card_nfc_uid', 'uuid_membre')
    
    # Rendre l'UUID lisible mais non modifiable dans le formulaire
    readonly_fields = ('uuid_membre',)
    
    # Ajouter des filtres sur le côté
    list_filter = ('statut', 'role')
    
    # Permettre la recherche par nom, prénom ou UID NFC
    search_fields = ('nom', 'prenom', 'card_nfc_uid')
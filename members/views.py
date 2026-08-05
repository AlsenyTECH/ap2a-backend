from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import Membre

@api_view(['GET'])
def check_card(models_request, identifier):
    """
    Cette fonction reçoit un identifiant (soit l'UUID du QR Code, soit l'UID NFC)
    et indique si la carte est valide ou refusée.
    """
    membre = None
    
    # 1. Chercher si l'identifiant correspond à un UUID (QR code) ou à un UID NFC
    try:
        membre = Membre.objects.filter(uuid_membre=identifier).first()
    except Exception:
        pass

    if not membre:
        membre = Membre.objects.filter(card_nfc_uid=identifier).first()

    # 2. Si le membre n'existe pas
    if not membre:
        return Response({
            "valide": False,
            "message": "Carte Inconnue / Non attribuée",
            "statut": "INCONNU"
        }, status=404)

    # 3. Vérifier le statut du membre
    est_valide = (membre.statut == 'ACTIF')
    
    return Response({
        "valide": est_valide,
        "nom": membre.nom,
        "prenom": membre.prenom,
        "role": membre.role,
        "statut": membre.statut,
        "message": "Accès Autorisé" if est_valide else f"Accès Refusé ({membre.statut})"
    })
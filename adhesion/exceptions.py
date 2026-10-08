"""
adhesion/exceptions.py

Format d'erreur unique de l'API : {"erreur": "<message lisible>"}, que
le portail affiche directement. Les erreurs de validation des
serializers DRF ({"champ": ["message"]}) y sont ramenées, en gardant le
détail par champ sous "champs".
"""

from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler


def _premier_message(detail):
    if isinstance(detail, dict):
        for valeur in detail.values():
            return _premier_message(valeur)
    if isinstance(detail, list) and detail:
        return _premier_message(detail[0])
    return str(detail)


def gestionnaire_exceptions(exc, context):
    reponse = exception_handler(exc, context)
    if reponse is not None and isinstance(exc, ValidationError):
        reponse.data = {"erreur": _premier_message(exc.detail), "champs": exc.detail}
    return reponse

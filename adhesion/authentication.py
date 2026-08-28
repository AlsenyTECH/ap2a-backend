"""
adhesion/authentication.py

Classe d'authentification personnalisée pour Django REST Framework.

Django REST Framework (DRF) ne sait authentifier que via des
mécanismes qu'on lui décrit explicitement. Comme notre Compte n'est
pas un utilisateur Django standard, on lui apprend ici comment
reconnaître un utilisateur à partir de l'en-tête HTTP "Authorization".

Format attendu dans les requêtes du client (Flutter) :
    Authorization: Bearer <cle_du_jeton>

Exception : l'export de rapports (/api/admin/rapports/export/) accepte
AUSSI le jeton en paramètre d'URL (?jeton=...). C'est un compromis
volontaire : un téléchargement de fichier déclenché depuis un
navigateur (ouverture directe d'un lien) ne peut pas ajouter d'en-tête
HTTP personnalisé - seul un paramètre d'URL fonctionne dans ce cas.
"""

from rest_framework import authentication, exceptions

from .models import Jeton


class AuthentificationParJeton(authentication.BaseAuthentication):

    def authenticate(self, request):
        """
        Appelée automatiquement par DRF sur CHAQUE requête entrante,
        avant même d'atteindre la vue. Doit retourner :
          - None si la requête ne tente pas de s'authentifier
            (DRF la traite alors comme anonyme)
          - un tuple (compte, None) si l'authentification réussit
          - lève AuthenticationFailed si une tentative échoue
        """
        entete = request.headers.get("Authorization")

        if entete:
            if not entete.startswith("Bearer "):
                raise exceptions.AuthenticationFailed(
                    "Format attendu : 'Authorization: Bearer <jeton>'"
                )
            cle = entete[len("Bearer "):].strip()
        else:
            # Repli : jeton en paramètre d'URL, uniquement pour les
            # téléchargements directs (voir docstring du module).
            cle = request.query_params.get("jeton")
            if not cle:
                return None  # requête anonyme, pas une erreur

        try:
            jeton = Jeton.objects.select_related("compte").get(cle=cle)
        except Jeton.DoesNotExist:
            raise exceptions.AuthenticationFailed("Jeton invalide ou expiré")

        compte = jeton.compte

        if compte.statut_compte != "ACTIF":
            raise exceptions.AuthenticationFailed("Ce compte est suspendu")

        return (compte, None)
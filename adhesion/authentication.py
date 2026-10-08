"""
adhesion/authentication.py

Classe d'authentification personnalisée pour Django REST Framework.

Django REST Framework (DRF) ne sait authentifier que via des
mécanismes qu'on lui décrit explicitement. Comme notre Compte n'est
pas un utilisateur Django standard, on lui apprend ici comment
reconnaître un utilisateur à partir de l'en-tête HTTP "Authorization".

Format attendu dans les requêtes du client :
    Authorization: Bearer <cle_du_jeton>

Exception : les routes de téléchargement direct (TELECHARGEMENTS_PAR_LIEN)
acceptent AUSSI un lien signé de courte durée en paramètre d'URL
(?telechargement=..., voir tickets.py). Un téléchargement déclenché en
ouvrant directement un lien ne peut pas ajouter d'en-tête HTTP ; mais
on n'y met plus JAMAIS le jeton de session lui-même, qui finirait dans
les journaux, l'historique du navigateur et l'en-tête Referer.
"""

from rest_framework import authentication, exceptions

from .models import Compte, Jeton
from .tickets import lire_lien_telechargement

# Noms des routes (adhesion/urls.py) téléchargeables par lien signé.
TELECHARGEMENTS_PAR_LIEN = {
    "exporter_rapport",
    "telecharger_certificat",
    "badges_cohorte_lot",
    "badge_participant",
    "certificats_cohorte_lot",
}


class AuthentificationParJeton(authentication.BaseAuthentication):

    def authenticate_header(self, request):
        # Fait répondre 401 (et non 403) aux requêtes non authentifiées,
        # pour que les clients sachent qu'il faut se (re)connecter.
        return 'Bearer realm="api"'

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

        if not entete:
            lien = request.query_params.get("telechargement")
            if lien:
                return self._authentifier_par_lien(request, lien)
            return None  # requête anonyme, pas une erreur

        if not entete.startswith("Bearer "):
            raise exceptions.AuthenticationFailed(
                "Format attendu : 'Authorization: Bearer <jeton>'"
            )
        cle = entete[len("Bearer "):].strip()

        try:
            jeton = Jeton.objects.select_related("compte").get(empreinte=Jeton.empreinte_de(cle))
        except Jeton.DoesNotExist:
            raise exceptions.AuthenticationFailed("Jeton invalide ou expiré")

        if jeton.est_expire:
            jeton.delete()
            raise exceptions.AuthenticationFailed("Session expirée, veuillez vous reconnecter")

        return (self._compte_actif(jeton.compte), None)

    def _authentifier_par_lien(self, request, lien):
        correspondance = request._request.resolver_match
        if (
            request.method != "GET"
            or correspondance is None
            or correspondance.url_name not in TELECHARGEMENTS_PAR_LIEN
        ):
            raise exceptions.AuthenticationFailed("Lien de téléchargement non valable pour cette route")

        id_compte = lire_lien_telechargement(lien, request.path)
        if id_compte is None:
            raise exceptions.AuthenticationFailed("Lien de téléchargement invalide ou expiré")
        try:
            compte = Compte.objects.get(id_compte=id_compte)
        except Compte.DoesNotExist:
            raise exceptions.AuthenticationFailed("Lien de téléchargement invalide ou expiré")
        return (self._compte_actif(compte), None)

    @staticmethod
    def _compte_actif(compte):
        if compte.statut_compte != "ACTIF":
            raise exceptions.AuthenticationFailed("Ce compte est suspendu")
        return compte
